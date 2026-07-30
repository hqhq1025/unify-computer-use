#!/usr/bin/env python3
"""验证两个控制平面之间的交接链路（待办 #20）。

浏览器归 Playwright/browser-use，桌面应用归本 MCP，两者的交接点是文件系统：
**浏览器下载 → `~/Downloads` → GUI 应用打开**。

这类跨平面任务在 OSWorld 里不少（下载附件后编辑、保存网页内容再处理），
而且是最容易碎的一环——两个平面各自都能跑通，接缝处却可能对不上：
下载目录不一致、文件还没落盘就去打开、应用打开了但 a11y 读不到内容。

判据全部用外部观测，不采信任一平面自己的返回值：
  1. 文件确实出现在下载目录（文件系统检查）
  2. GUI 应用确实打开了它（窗口标题）
  3. 内容确实能通过 a11y 读到（AT-SPI 真值）

前置：Chrome 需带 --remote-debugging-port 启动；需要 playwright。

用法:
  scripts/verify-cross-plane-handoff.py
  scripts/verify-cross-plane-handoff.py --cdp http://127.0.0.1:9222 --editor gedit
"""

import argparse
import http.server
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")
MARKER = "cross-plane-handoff-marker"

# 浏览器导航跑在独立子进程里：触发下载会中断导航，Playwright 同步 API
# 在这种情形下可能卡在 close()，隔离后挂死也不影响主流程判定。
NAVIGATE_SNIPPET = r"""
import sys
from playwright.sync_api import sync_playwright

cdp, url, dest = sys.argv[1], sys.argv[2], sys.argv[3]
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(cdp)
    ctxs = browser.contexts
    page = (ctxs[0].pages or [ctxs[0].new_page()])[0]
    # CDP 驱动的导航没有用户手势，Chrome 会静默拦下下载——不报错、不弹提示、
    # 什么都不留。必须用 Page.setDownloadBehavior 显式放行并指定落盘目录。
    session = page.context.new_cdp_session(page)
    session.send("Page.setDownloadBehavior",
                 {"behavior": "allow", "downloadPath": dest})
    try:
        page.goto(url, timeout=15000)
    except Exception:
        pass          # 下载会中断导航，属预期
"""


def serve(root):
    """本地服务，强制以附件形式下发。

    不加 Content-Disposition 的话 Chrome 会把 .txt 直接内联渲染，
    根本不触发下载——那样测的就不是交接链路了。
    """

    def end_headers(self):
        self.send_header("Content-Disposition", "attachment")
        http.server.SimpleHTTPRequestHandler.end_headers(self)

    handler = type(
        "Quiet", (http.server.SimpleHTTPRequestHandler,),
        {"log_message": lambda *a, **k: None,
         "end_headers": end_headers,
         "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(
             self, *a, directory=root, **k)},
    )
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:{}".format(server.server_address[1])


def window_titles():
    try:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    return [line.split(None, 3)[-1] for line in out.splitlines()]


def atspi_text_contains(app_name, needle):
    """直接读 AT-SPI，确认应用里真的能看到这段内容。"""
    snippet = r'''
import sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

target, needle = sys.argv[1], sys.argv[2]

def safe(call, default=None):
    try:
        value = call()
        return default if value is None else value
    except Exception:
        return default

desktop = Atspi.get_desktop(0)
app = None
for index in range(safe(desktop.get_child_count, 0) or 0):
    candidate = safe(lambda index=index: desktop.get_child_at_index(index))
    if candidate is not None and safe(candidate.get_name, "") == target:
        app = candidate

hit = False

def visit(node, depth=0):
    global hit
    if depth > 30 or hit:
        return
    iface = safe(node.get_text_iface)
    if iface is not None:
        count = safe(lambda: Atspi.Text.get_character_count(iface), 0) or 0
        if count:
            text = safe(lambda: Atspi.Text.get_text(iface, 0, count), "") or ""
            if needle in text:
                hit = True
                return
    for index in range(safe(node.get_child_count, 0) or 0):
        child = safe(lambda index=index: node.get_child_at_index(index))
        if child is not None:
            visit(child, depth + 1)

if app is not None:
    visit(app)
print("HIT" if hit else "MISS")
'''
    try:
        result = subprocess.run([sys.executable, "-c", snippet, app_name, needle],
                                capture_output=True, text=True, timeout=120)
        return result.stdout.strip().endswith("HIT")
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="验证浏览器与桌面两个控制平面的交接")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--editor", default="gedit", help="用哪个 GUI 应用打开下载的文件")
    parser.add_argument("--downloads", default=os.path.expanduser("~/Downloads"))
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright：pip install playwright", file=sys.stderr)
        return 2

    root = tempfile.mkdtemp(prefix="ocu-handoff-")
    # 文件名每次唯一。重名会让 Chrome 走"已存在"分支（追加 (1) 或弹提示），
    # 轮询固定名字就再也等不到——表现为"下载没发生"，其实是名字对不上。
    name = "handoff-{}.txt".format(os.path.basename(root).rsplit("-", 1)[-1])
    with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
        handle.write("{}\nline two\n".format(MARKER))
    server, base = serve(root)

    os.makedirs(args.downloads, exist_ok=True)
    landed = os.path.join(args.downloads, name)
    if os.path.exists(landed):
        os.remove(landed)

    failures = []
    chrome_downloads = os.path.expanduser("~/Downloads")
    try:
        # --- 平面一：浏览器下载 ---
        # 两点设计：
        # 1. 不用 Playwright 的 expect_download。对 CDP 接管的浏览器，context
        #    不是它创建的、acceptDownloads 没配上，download 事件不一定触发。
        #    让 Chrome 原生下载再轮询文件系统，也更贴近真实场景。
        # 2. 浏览器那步放进独立子进程并设超时。触发下载会中断导航，
        #    Playwright 的同步 API 在这种情形下可能卡在 close()——
        #    不隔离的话整个脚本会挂死，而下载其实已经成功了。
        nav = subprocess.run(
            [sys.executable, "-c", NAVIGATE_SNIPPET, args.cdp,
             "{}/{}".format(base, name), chrome_downloads],
            capture_output=True, text=True, timeout=90,
        )
        if nav.returncode != 0:
            tail = [l for l in (nav.stderr or "").splitlines() if l.strip()][-1:] or [""]
            print("  浏览器步骤失败（返回码 {}）：{}".format(nav.returncode, tail[0][:120]))
        else:
            print("  已让浏览器导航到 {}/{}".format(base, name))

        native = os.path.join(chrome_downloads, name)
        for _ in range(24):
            time.sleep(1.0)
            if os.path.exists(native) and not os.path.exists(native + ".crdownload"):
                break
        if os.path.exists(native):
            landed = native

        if os.path.exists(landed) and MARKER in open(landed, encoding="utf-8").read():
            print("  PASS  文件已落到下载目录：{}".format(landed))
        else:
            failures.append("文件没有落到下载目录")
            print("  FAIL  文件没有落到下载目录")
            return 1

        # --- 交接：用 GUI 应用打开 ---
        subprocess.Popen("setsid {} {} </dev/null >/dev/null 2>&1 &".format(args.editor, landed),
                         shell=True, start_new_session=True)
        opened = False
        for _ in range(20):
            time.sleep(1.5)
            if any(name in title for title in window_titles()):
                opened = True
                break
        if opened:
            print("  PASS  {} 打开了该文件（窗口标题可见）".format(args.editor))
        else:
            failures.append("GUI 应用没有打开该文件")
            print("  FAIL  {} 没有打开该文件，窗口：{}".format(args.editor, window_titles()))

        # --- 平面二：a11y 能读到内容 ---
        if opened:
            time.sleep(2)
            if atspi_text_contains(args.editor, MARKER):
                print("  PASS  a11y 树里读到了文件内容")
            else:
                failures.append("a11y 读不到文件内容")
                print("  FAIL  a11y 读不到文件内容")
    finally:
        # shutdown() 可能阻塞在正在处理的请求上；服务线程是 daemon，
        # 进程退出时自然回收，这里不值得为它挂住整个脚本。
        threading.Thread(target=server.shutdown, daemon=True).start()
        subprocess.run(["pkill", "-9", "-f", "{} {}".format(args.editor, landed)],
                       capture_output=True, timeout=30)

    print()
    if failures:
        print("失败 {} 项：{}".format(len(failures), "；".join(failures)))
        return 1
    print("全部通过：浏览器下载 → 下载目录 → GUI 应用打开 → a11y 可读，交接链路完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
