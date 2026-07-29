#!/usr/bin/env python3
"""验证 Playwright 是 attach 到环境里那个 Chrome，而不是自己 launch 一个。

为什么这件事必须单独验证：OSWorld 有一批验证器会检查 Chrome 的 profile
（书签、历史、下载记录、打开的标签页）。如果 Playwright 起了自己的浏览器实例，
agent 在里面做得再对，验证器查的仍是环境那个 Chrome，看到的是空的，直接判 0 分。
这类失败不会报错，只会静默失分——跑完一整轮才发现全白做。

判据不是"Playwright 能跑通"，而是能否从外部独立证明操作的就是环境实例：
  1. 接管后能看到环境实例的 context / page
  2. 通过 Playwright 导航后，**环境实例的窗口标题**跟着变（用 wmctrl 独立观测，
     不依赖 Playwright 自己的返回值）
  3. 全程 Chrome 进程数不暴涨（没有第二个浏览器被拉起）
  4. 断开 CDP 后环境实例仍存活（断开不应误杀环境）

前置：Chrome 必须带 --remote-debugging-port 启动。注意 Chrome 的会话交接——
若已有实例在跑，带新参数的启动命令会被交接过去、参数完全失效，必须先彻底
杀干净或使用独立 --user-data-dir。

用法:
  scripts/verify-browser-cdp-attach.py                       # 只验 Playwright
  scripts/verify-browser-cdp-attach.py --browser-use-python ~/.venvs/browseruse/bin/python
  scripts/verify-browser-cdp-attach.py --cdp http://127.0.0.1:9222

browser-use 需要 Python >=3.11，通常和仓库其它脚本不在同一个解释器里，所以用
`--browser-use-python` 指过去，脚本会用该解释器跑同一套判据。它默认会拦截
file:// 导航（SecurityWatchdog），因此这部分探针走本地 HTTP 服务而非本地文件。
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

CHROME_PROC_PATTERN = "[/]opt/google/chrome/chrome"

BROWSER_USE_SNIPPET = r'''
import asyncio, subprocess, sys

BASE = sys.argv[1]
CDP = sys.argv[2]

def procs():
    return int(subprocess.run("ps -eo args | grep -c '{pattern}'",
               shell=True, capture_output=True, text=True).stdout.strip() or 0)

def titles():
    out = subprocess.run(["wmctrl","-l"], capture_output=True, text=True).stdout
    return [l.split(None,3)[-1] for l in out.splitlines() if "Chrome" in l]

async def run():
    from browser_use import Browser
    fails = []
    before = procs()
    browser = Browser(cdp_url=CDP)
    await browser.start()
    print("  PASS  browser-use 连上 CDP")
    for page, expect in (("one.html","OCU BU Probe One"), ("two.html","OCU BU Probe Two")):
        await browser.navigate_to(BASE + "/" + page)
        await asyncio.sleep(2.5)
        t = titles()
        if any(expect in x for x in t):
            print("  PASS  browser-use 导航后环境实例窗口标题变为 %r" % expect)
        else:
            fails.append("browser-use 窗口标题未变: %s" % t)
            print("  FAIL  browser-use 窗口标题未变: %s" % t)
    during = procs()
    if during <= before + 3:
        print("  PASS  browser-use 未另起浏览器实例（%d -> %d）" % (before, during))
    else:
        fails.append("browser-use 进程暴涨 %d->%d" % (before, during))
        print("  FAIL  browser-use 进程暴涨 %d -> %d" % (before, during))
    await browser.stop()
    await asyncio.sleep(2)
    if procs() > 0:
        print("  PASS  browser-use 断开后环境实例仍存活")
    else:
        fails.append("browser-use 断开把环境 Chrome 关掉了")
        print("  FAIL  browser-use 断开把环境 Chrome 关掉了")
    return fails

async def main():
    try:
        fails = await asyncio.wait_for(run(), timeout=150)
    except asyncio.TimeoutError:
        print("  FAIL  browser-use 检查超时"); return 1
    return 1 if fails else 0

raise SystemExit(asyncio.run(main()))
'''.replace("{pattern}", CHROME_PROC_PATTERN)


def chrome_procs():
    out = subprocess.run(
        "ps -eo args | grep -c '{}'".format(CHROME_PROC_PATTERN),
        shell=True, capture_output=True, text=True,
    ).stdout.strip()
    try:
        return int(out or 0)
    except ValueError:
        return 0


def chrome_window_titles():
    """用 wmctrl 从窗口管理器侧独立观测，不采信 Playwright 自己的说法。"""
    try:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    return [line.split(None, 3)[-1] for line in out.splitlines() if "Chrome" in line]


def write_page(path, title, body):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "<html><head><title>{}</title></head><body><h1>{}</h1></body></html>".format(
                title, body
            )
        )


def serve_probe_pages():
    """起一个临时 HTTP 服务放探针页面。

    browser-use 默认拦截 file:// 导航，所以探针不能用本地文件。
    """
    root = tempfile.mkdtemp(prefix="ocu-bu-pages-")
    for name, title in (("one.html", "OCU BU Probe One"), ("two.html", "OCU BU Probe Two")):
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write("<html><head><title>{}</title></head><body><h1>{}</h1>"
                         "</body></html>".format(title, name))

    handler = type(
        "Quiet", (http.server.SimpleHTTPRequestHandler,),
        {"log_message": lambda *a, **k: None,
         "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(
             self, *a, directory=root, **k)},
    )
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:{}".format(server.server_address[1])


def check_browser_use(python_path, cdp):
    """用指定解释器跑同一套判据验证 browser-use 的 attach 行为。"""
    if not os.path.exists(python_path):
        print("  SKIP  browser-use：解释器不存在 {}".format(python_path))
        return []
    server, base = serve_probe_pages()
    try:
        print("\n--- browser-use（解释器 {}）---".format(python_path))
        result = subprocess.run(
            [python_path, "-c", BROWSER_USE_SNIPPET, base, cdp],
            capture_output=True, text=True, timeout=300,
        )
        for line in result.stdout.splitlines():
            if line.strip().startswith(("PASS", "FAIL", "  PASS", "  FAIL")):
                print(line)
        if result.returncode != 0:
            tail = [l for l in result.stderr.splitlines() if l.strip()][-2:]
            if tail:
                print("  (stderr) " + " | ".join(tail))
            return ["browser-use attach 检查未通过"]
        return []
    except subprocess.TimeoutExpired:
        print("  FAIL  browser-use 检查超时")
        return ["browser-use 检查超时"]
    finally:
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="验证浏览器自动化工具接管的是环境里的 Chrome")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222", help="CDP 端点")
    parser.add_argument(
        "--browser-use-python", default=None,
        help="带 browser-use 的 Python 解释器路径（需 >=3.11）。不给则跳过该检查",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright：pip install playwright（无需 playwright install，"
              "attach 模式不需要自带浏览器）", file=sys.stderr)
        return 2

    tmpdir = tempfile.mkdtemp(prefix="ocu-cdp-")
    first = os.path.join(tmpdir, "first.html")
    second = os.path.join(tmpdir, "second.html")
    write_page(first, "OCU CDP Probe One", "one")
    write_page(second, "OCU CDP Probe Two", "two")

    failures = []
    before = chrome_procs()
    if before == 0:
        print("环境里没有运行中的 Chrome。请先带 --remote-debugging-port 启动。",
              file=sys.stderr)
        return 2
    print("接管前：Chrome 进程 {}，窗口 {}".format(before, chrome_window_titles()))

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp)
        except Exception as exc:
            print("连接 CDP 失败 {}: {}".format(args.cdp, exc), file=sys.stderr)
            return 2

        pages = [page for context in browser.contexts for page in context.pages]
        print("\n接管成功：context {} 个，page {} 个".format(len(browser.contexts), len(pages)))
        if pages:
            print("  PASS  能看到环境实例的 page")
        else:
            failures.append("接管后看不到任何 page")
            print("  FAIL  接管后看不到任何 page")
            browser.close()
            return 1

        page = pages[0]
        for path, expect in ((first, "OCU CDP Probe One"), (second, "OCU CDP Probe Two")):
            page.goto("file://" + path, wait_until="load")
            time.sleep(2)
            titles = chrome_window_titles()
            if any(expect in title for title in titles):
                print("  PASS  导航后环境实例窗口标题变为 {!r}".format(expect))
            else:
                failures.append("窗口标题未跟随导航变化（期望 {!r}，实际 {}）".format(expect, titles))
                print("  FAIL  窗口标题未跟随导航变化：{}".format(titles))

        during = chrome_procs()
        if during <= before + 3:
            print("  PASS  未另起浏览器实例（进程 {} -> {}）".format(before, during))
        else:
            failures.append("进程数暴涨 {} -> {}，疑似另起了浏览器".format(before, during))
            print("  FAIL  进程数暴涨 {} -> {}".format(before, during))

        try:
            text = page.inner_text("h1")
            if text == "two":
                print("  PASS  能读到页面 DOM（h1={!r}）".format(text))
            else:
                failures.append("DOM 内容不符：{!r}".format(text))
                print("  FAIL  DOM 内容不符：{!r}".format(text))
        except Exception as exc:
            failures.append("读取 DOM 失败：{}".format(exc))
            print("  FAIL  读取 DOM 失败：{}".format(exc))

        browser.close()

    time.sleep(2)
    after = chrome_procs()
    if after > 0:
        print("  PASS  断开 CDP 后环境实例仍存活（进程 {}）".format(after))
    else:
        failures.append("断开 CDP 把环境里的 Chrome 一起关掉了")
        print("  FAIL  断开 CDP 把环境里的 Chrome 一起关掉了")

    if args.browser_use_python:
        failures.extend(check_browser_use(args.browser_use_python, args.cdp))

    print()
    if failures:
        print("失败 {} 项：".format(len(failures)))
        for item in failures:
            print("  -", item)
        return 1
    print("全部通过：接管的是环境实例本身，不是新起的浏览器")
    return 0


if __name__ == "__main__":
    sys.exit(main())
