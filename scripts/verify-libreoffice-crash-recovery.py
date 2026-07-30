#!/usr/bin/env python3
"""验证 LibreOffice 中途消失后能自动恢复并续跑同一任务（待办 #30）。

LibreOffice 占 OSWorld 370 个任务里的 117 个，而它在 AT-SPI 驱动下反复开关
模态对话框时会**静默消失**——内核层面没有 segfault、没有 OOM、没有 core dump，
stderr 戛然而止，进程就没了。实测：带下拉交互的对话框循环活 3 轮，
只开关对话框活 4 轮（详见 plan #30 的排查记录）。

这不是 runtime 能修的，是 harness 必须具备的能力。缺了它，一次崩溃就等于
整个任务判失败——而任务本身可能只差最后一步。

本脚本把这条能力做成可复用的例程并当场验证：

  1. 开始任务：打开文档、改行距
  2. **中途杀掉 LibreOffice**（模拟真实崩溃）
  3. 自动恢复：重启 → 处理「文档恢复」对话框（Discard → Yes 两级）
  4. 续跑任务：重新改行距 → 保存
  5. **判分器级验收**：读保存后的 content.xml，确认 line-height 生效

判据全部用外部真值，不采信任一工具的返回值。

用法:
  scripts/verify-libreoffice-crash-recovery.py
  scripts/verify-libreoffice-crash-recovery.py --keep    # 不清理产物，便于排查
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")


class MCP:
    """最小 stdio JSON-RPC 客户端。与 record-trajectory.py 同构。"""

    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._id = 0
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for _ in self.process.stderr:
            pass

    def send(self, method, params=None, notify=False, timeout=180):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self._id += 1
            message["id"] = self._id
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError("MCP 进程已退出")
        if notify:
            return None
        box = {}
        thread = threading.Thread(
            target=lambda: box.setdefault("line", self.process.stdout.readline()),
            daemon=True,
        )
        thread.start()
        thread.join(timeout)
        if not box.get("line"):
            raise RuntimeError("MCP 无响应: {}".format(method))
        return json.loads(box["line"])

    def handshake(self):
        self.send("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "crash-recovery", "version": "1"},
        })
        self.send("notifications/initialized", {}, notify=True)

    def call(self, name, arguments):
        response = self.send("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result", {})
        text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        return text, bool(result.get("isError"))

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


def soffice_alive():
    out = subprocess.run(["pgrep", "-f", "soffice.bin"],
                         capture_output=True, text=True).stdout.strip()
    return bool(out)


def launch(document, settle=70):
    """启动 LibreOffice 并等到进程真的起来。

    实测启动经常需要 40~70 秒（首次更久），而且偶尔第一次拉不起来，
    所以这里重试而不是等一个固定时长就宣告失败。
    """
    for attempt in range(3):
        subprocess.Popen(
            "setsid soffice {} </dev/null >/dev/null 2>&1 &".format(document),
            shell=True, start_new_session=True,
        )
        deadline = time.time() + settle
        while time.time() < deadline:
            if soffice_alive():
                time.sleep(6)          # 再给窗口一点时间落地
                return True
            time.sleep(2)
    return False


def find_index(tree, pattern):
    for line in tree.splitlines():
        stripped = line.strip()
        if not (stripped and stripped[0].isdigit()):
            continue
        # 新文法给自由文本加了引号（修"名字可以含冒号"造成的歧义）。
        # 匹配时先去引号，这样 r"menu item Rename" 这类既有模式不必为引号重写。
        plain = stripped.replace('"', "")
        if re.search(pattern, plain):
            return stripped.split(" ", 1)[0]
    return None


def window_title(tree):
    lines = tree.splitlines()
    return lines[1] if len(lines) > 1 else ""


def dismiss_recovery(client, app, budget=120):
    """处理崩溃后必然出现的「文档恢复」对话框。

    它是两级的：Discard 之后还有一个 Question 确认框（Yes）。不处理的话
    它会**挡住后续所有操作**——而且 agent 看到的树是这个对话框的树，
    与任务毫无关系，很容易被误判成"应用坏了"。

    另外首次打开 CSV 之类的格式还会插入 Text Import 对话框，一并在这里过掉。
    """
    handled = []
    # 对话框可能连着来好几轮：崩溃两次就有两条恢复记录，Discard→Yes 之后
    # 还会再弹一次。所以按**时间预算**跑，而不是数固定轮数。
    deadline = time.time() + budget
    stalled = 0
    title = ""
    while time.time() < deadline and stalled < 6:
        tree, error = client.call("get_app_state", {"app": app})
        if error:
            stalled += 1
            time.sleep(1.5)
            continue
        title = window_title(tree)
        target = None
        if "Recovery" in title:
            # **恢复流程有两个不同的窗口，标题里都带 Recovery，按钮却不同：**
            #   「LibreOffice 7.3 Document Recovery」 -> Start / Discard
            #   「LibreOffice Document Recovery」（自动恢复被中断）-> Save / Cancel
            # 只认 Discard 会在第二个窗口上无限空转——实测就是卡在这里。
            # 所以按"哪个退出按钮存在"来选，而不是假定只有一种。
            if find_index(tree, r"push button Discard"):
                target, label = r"push button Discard", "Discard"
            elif find_index(tree, r"push button Cancel"):
                target, label = r"push button Cancel", "Cancel"
            else:
                target, label = r"push button (Close|OK)", "Close"
        elif "Question" in title or "Alert" in title:
            target, label = r"push button Yes", "Yes"
        elif "Text Import" in title:
            target, label = r"push button OK", "Import OK"
        else:
            return handled, title          # 已经是正常窗口

        index = find_index(tree, target)
        if index is None:
            # 读到的是中间态：标题已经换了、按钮还没出来。**重读，不要当成终态**，
            # 更不要因为"没找到按钮"就重复点上一步——实测重复点 Discard 会叠出
            # 一串一模一样的确认框，越点越乱（手动排查时踩过这个坑）。
            stalled += 1
            time.sleep(1.5)
            continue

        stalled = 0
        client.call("click", {"app": app, "element_index": index,
                              "click_method": "auto"})
        handled.append(label)
        # 等标题真的变了再进下一轮，避免对着同一个对话框连点
        settled = time.time() + 15
        while time.time() < settled:
            time.sleep(1.0)
            probe, probe_error = client.call("get_app_state", {"app": app})
            if probe_error:
                continue
            if window_title(probe) != title:
                break
    return handled, title


def set_line_spacing_double(client, app):
    """全选 → 格式 → 段落 → 行距下拉 → Double → 确定。

    下拉项那一步必须由 auto 自己识别成「弹窗内的 table cell」并走坐标——
    语义调用会关掉下拉却不提交值。详见 runtime.is_dropdown_item()。
    """
    client.call("press_key", {"app": app, "key": "ctrl+a"})
    time.sleep(1)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 1500})
    index = find_index(tree, r"menu Format")
    if not index:
        return False, "找不到 Format 菜单"
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(1.5)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 1500})
    index = find_index(tree, r"menu item Paragraph")
    if not index:
        return False, "找不到 Paragraph 菜单项"
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(2.5)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 1500})
    index = find_index(tree, r"toggle button")
    if not index:
        return False, "找不到行距下拉的 toggle button"
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(1.8)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 1500})
    index = find_index(tree, r"table cell Double")
    if not index:
        return False, "下拉里找不到 Double"
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(1.5)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 1500})
    index = find_index(tree, r"push button OK")
    if not index:
        return False, "找不到 OK 按钮"
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(2.5)
    return True, "已应用"


def line_height_of(path):
    """读保存后的 ODT，取 content.xml 里的 line-height。

    这是 OSWorld 判分器实际读取的东西——不采信任何工具的返回值。
    """
    if not os.path.exists(path):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            content = archive.read("content.xml").decode("utf-8", "replace")
    except Exception:
        return None
    found = re.findall(r'line-height="([^"]+)"', content)
    return found[0] if found else None


def main():
    parser = argparse.ArgumentParser(
        description="验证 LibreOffice 崩溃后能自动恢复并续跑同一任务")
    parser.add_argument("--binary", default=DEFAULT_BIN)
    parser.add_argument("--keep", action="store_true", help="保留产物便于排查")
    args = parser.parse_args()

    if not os.path.exists(args.binary):
        print("找不到可执行文件 {}".format(args.binary), file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="ocu-crash-")
    source = os.path.join(workdir, "recovery-drill.odt")
    seed = os.path.join(workdir, "recovery-drill.txt")
    with open(seed, "w", encoding="utf-8") as handle:
        handle.write("First line.\nSecond line.\nThird line.\n")

    subprocess.run(["pkill", "-9", "-f", "soffice"], capture_output=True)
    time.sleep(4)
    convert = subprocess.run(
        ["soffice", "--headless", "--convert-to", "odt", "--outdir", workdir, seed],
        capture_output=True, text=True, timeout=300,
    )
    if not os.path.exists(source):
        print("无法生成 ODT：{}".format((convert.stderr or "")[:160]), file=sys.stderr)
        return 2
    print("准备文档 {}（初始 line-height={}）".format(source, line_height_of(source)))

    failures = []
    client = None
    try:
        subprocess.run(["pkill", "-9", "-f", "soffice"], capture_output=True)
        time.sleep(4)
        if not launch(source):
            print("FAIL  LibreOffice 起不来")
            return 1

        client = MCP(args.binary)
        client.handshake()
        handled, title = dismiss_recovery(client, "soffice")
        print("  首次就绪，窗口 {}（过掉 {}）".format(title[:52], handled or "无对话框"))

        # --- 1. 任务进行到一半 ---
        client.call("press_key", {"app": "soffice", "key": "ctrl+a"})
        time.sleep(1)
        print("  任务已开始（全选）")

        # --- 2. 中途杀掉，模拟真实崩溃 ---
        subprocess.run(["pkill", "-9", "-f", "soffice.bin"], capture_output=True)
        time.sleep(4)
        if soffice_alive():
            failures.append("没能杀掉 LibreOffice，实验前提不成立")
        else:
            print("  已在任务中途杀掉 LibreOffice")

        # 崩溃后动作应当明确失败，而不是静默成功
        _, error = client.call("press_key", {"app": "soffice", "key": "End"})
        if error:
            print("  PASS  应用消失后动作明确失败，没有静默成功")
        else:
            failures.append("应用已消失，动作却报成功")
            print("  FAIL  应用已消失，动作却报成功")

        # --- 3. 自动恢复 ---
        client.close()
        if not launch(source):
            failures.append("恢复阶段 LibreOffice 起不来")
            print("  FAIL  恢复阶段起不来")
            return 1
        client = MCP(args.binary)
        client.handshake()
        handled, title = dismiss_recovery(client, "soffice")
        if "Recovery" in title or "Question" in title:
            failures.append("恢复对话框没能过掉：{}".format(title))
            print("  FAIL  恢复对话框仍在：{}".format(title[:60]))
        else:
            print("  PASS  已自动恢复（过掉 {}），窗口 {}".format(
                handled or "无对话框", title[:46]))

        # --- 4. 续跑同一任务 ---
        ok, detail = set_line_spacing_double(client, "soffice")
        if not ok:
            failures.append("续跑失败：{}".format(detail))
            print("  FAIL  续跑失败：{}".format(detail))
        else:
            client.call("press_key", {"app": "soffice", "key": "ctrl+s"})
            time.sleep(4)
            print("  已续跑并保存")

        # --- 5. 判分器级验收 ---
        height = line_height_of(source)
        if height == "200%":
            print("  PASS  保存后的 content.xml line-height={}".format(height))
        else:
            failures.append("文档最终 line-height={}，期望 200%".format(height))
            print("  FAIL  文档最终 line-height={}".format(height))
    finally:
        if client is not None:
            client.close()
        subprocess.run(["pkill", "-9", "-f", "soffice"], capture_output=True)
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print("产物保留在 {}".format(workdir))

    print()
    if failures:
        print("失败 {} 项：{}".format(len(failures), "；".join(failures)))
        return 1
    print("全部通过：崩溃 → 自动重启 → 过掉恢复对话框 → 续跑同一任务 → 判分器级验收")
    return 0


if __name__ == "__main__":
    sys.exit(main())
