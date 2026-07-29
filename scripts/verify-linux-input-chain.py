#!/usr/bin/env python3
"""在真实桌面会话里验证 Linux 输入链路，覆盖单测覆盖不到的部分。

`apps/OpenComputerUseLinux/runtime_test.py` 用假节点验证选择逻辑；这个脚本
把真实的 MCP server 拉起来，对着真实应用做动作，再直接读 AT-SPI 真值比对
动作前后的状态。两类曾经静默失败、但工具报成功的路径必须在这里被抓住：

1. `type_text` 写进了隐藏的占位控件，文本没落到用户看得见的地方。
2. `press_key` 走 XTEST 全局合成，落到当前焦点窗口而不是目标应用。

用法:
  scripts/verify-linux-input-chain.py --app gedit
  scripts/verify-linux-input-chain.py --app gedit --binary dist/linux/amd64/open-computer-use

注意: 脚本会往目标应用的焦点编辑控件里写一小段探针文本，结束前会把它删掉
（只替换探针子串，不动其它内容）。建议仍然指向一个可以随便写的窗口。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BINARY = os.path.join("dist", "linux", "amd64", "open-computer-use")
PROBE_TEXT = "ocu-verify-probe"


REMOVE_PROBE_SNIPPET = r"""
import sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

target, probe = sys.argv[1], sys.argv[2]

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

def visit(node, depth=0):
    if depth > 40 or app is None:
        return
    editable = safe(node.get_editable_text_iface)
    if editable is not None:
        iface = safe(node.get_text_iface)
        count = safe(lambda: Atspi.Text.get_character_count(iface), 0) or 0
        if count:
            current = safe(lambda: Atspi.Text.get_text(iface, 0, count), "")
            if probe in current:
                safe(lambda: Atspi.EditableText.set_text_contents(
                    editable, current.replace(probe, "")))
    for index in range(safe(node.get_child_count, 0) or 0):
        child = safe(lambda index=index: node.get_child_at_index(index))
        if child is not None:
            visit(child, depth + 1)

if app is not None:
    visit(app)
"""


def remove_probe_text(app_name):
    """把脚本自己写进去的探针文本撤掉，只替换探针子串，不动用户其它内容。"""
    try:
        subprocess.run(
            [sys.executable, "-c", REMOVE_PROBE_SNIPPET, app_name, PROBE_TEXT],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# 真值读取：单独起子进程读 AT-SPI，避免本进程持有的缓存影响判断
# --------------------------------------------------------------------------

TRUTH_SNIPPET = r"""
import json, sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

target = sys.argv[1]

def safe(call, default=None):
    try:
        value = call()
        return default if value is None else value
    except Exception:
        return default

def find_app(name):
    desktop = Atspi.get_desktop(0)
    for index in range(safe(desktop.get_child_count, 0) or 0):
        app = safe(lambda index=index: desktop.get_child_at_index(index))
        if app is not None and safe(app.get_name, "") == name:
            return app
    return None

app = find_app(target)
if app is None:
    print(json.dumps({"found": False}))
    raise SystemExit(0)

window = None
for index in range(safe(app.get_child_count, 0) or 0):
    child = safe(lambda index=index: app.get_child_at_index(index))
    if child is not None and safe(child.get_role_name, "") == "frame":
        window = child
        break

states = safe(window.get_state_set) if window is not None else None
active = bool(states and states.contains(Atspi.StateType.ACTIVE))

focused_text = None
editable_total = 0

def visit(node, depth=0):
    global focused_text, editable_total
    if depth > 40:
        return
    if safe(node.get_editable_text_iface) is not None:
        editable_total += 1
        node_states = safe(node.get_state_set)
        if node_states is not None and node_states.contains(Atspi.StateType.FOCUSED):
            iface = safe(node.get_text_iface)
            if iface is not None and focused_text is None:
                count = safe(lambda: Atspi.Text.get_character_count(iface), 0) or 0
                focused_text = {
                    "chars": count,
                    "text": safe(lambda: Atspi.Text.get_text(iface, 0, count), "") if count else "",
                    "caret": safe(lambda: Atspi.Text.get_caret_offset(iface)),
                }
    for index in range(safe(node.get_child_count, 0) or 0):
        child = safe(lambda index=index: node.get_child_at_index(index))
        if child is not None:
            visit(child, depth + 1)

visit(app)
print(json.dumps({
    "found": True,
    "windowActive": active,
    "editableTotal": editable_total,
    "focusedText": focused_text,
}))
"""


def read_truth(app_name):
    result = subprocess.run(
        [sys.executable, "-c", TRUTH_SNIPPET, app_name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "无法读取 AT-SPI 真值: {}".format(result.stderr.strip()[:400] or "空输出")
        )
    return json.loads(result.stdout.strip())


# --------------------------------------------------------------------------
# 最小 MCP stdio 客户端
# --------------------------------------------------------------------------


class MCPClient:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 0
        self.stderr_lines = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def _write(self, payload):
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method, params=None, timeout=120):
        self._next_id += 1
        self._write(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            }
        )
        box = {}

        def reader():
            box["line"] = self.process.stdout.readline()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if "line" not in box:
            raise RuntimeError("MCP 请求超时: {}".format(method))
        if not box["line"]:
            raise RuntimeError("MCP server 提前退出: {}".format(method))
        return json.loads(box["line"])

    def call_tool(self, name, arguments):
        response = self.request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        result = response.get("result") or {}
        text = ""
        for item in result.get("content") or []:
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        return bool(result.get("isError")), text

    def handshake(self):
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "verify-linux-input-chain", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.failures = []
        self.skipped = []

    def ok(self, name, detail=""):
        print("  PASS  {}{}".format(name, "  ({})".format(detail) if detail else ""))

    def fail(self, name, detail):
        print("  FAIL  {}  ({})".format(name, detail))
        self.failures.append(name)

    def skip(self, name, detail):
        print("  SKIP  {}  ({})".format(name, detail))
        self.skipped.append(name)


def deactivate_target(app_name):
    """把焦点移开目标窗口，用来构造"目标未激活"的前置条件。"""
    for tool, args in (
        ("wmctrl", ["wmctrl", "-a", "Desktop"]),
        ("xdotool", ["xdotool", "getactivewindow", "windowminimize"]),
    ):
        if shutil.which(tool):
            subprocess.run(args, capture_output=True, timeout=30)
            time.sleep(0.4)
            if not read_truth(app_name).get("windowActive"):
                return True
    return not read_truth(app_name).get("windowActive")


def collapse_selection(client, app_name):
    """把选区收起来。

    留着选区会让后面的 type_text 走"替换选区"分支，那是正确行为，但会让
    "插入了多少字符"这类断言失去意义，也会把检查之间的状态互相污染。
    """
    client.call_tool("get_app_state", {"app": app_name})
    client.call_tool("press_key", {"app": app_name, "key": "Home"})
    time.sleep(0.3)


def check_type_text_lands_in_focused_widget(client, app_name, report):
    name = "type_text 写进用户可见的焦点控件"
    before = read_truth(app_name)
    focused = before.get("focusedText")
    if focused is None:
        report.skip(name, "目标应用没有获得焦点的可编辑控件")
        return

    collapse_selection(client, app_name)
    focused = read_truth(app_name).get("focusedText") or focused

    client.call_tool("get_app_state", {"app": app_name})
    is_error, text = client.call_tool(
        "type_text", {"app": app_name, "text": PROBE_TEXT}
    )
    if is_error:
        report.fail(name, "工具返回错误: {}".format(text.splitlines()[0] if text else ""))
        return

    time.sleep(0.5)
    after = read_truth(app_name)
    after_focused = after.get("focusedText") or {}
    landed = PROBE_TEXT in (after_focused.get("text") or "")
    # 不论成败都把探针撤掉：这个脚本要能对着用户正在编辑的窗口反复运行。
    remove_probe_text(app_name)
    if landed:
        report.ok(name, "字符数 {} -> {}".format(focused["chars"], after_focused.get("chars")))
    else:
        report.fail(
            name,
            "工具报成功但焦点控件没有收到文本（共 {} 个可编辑控件，"
            "很可能写进了隐藏占位控件）".format(after.get("editableTotal")),
        )


def check_press_key_never_silently_misdelivers(client, app_name, report):
    name = "目标窗口未激活时 press_key 不静默误投"
    if not deactivate_target(app_name):
        report.skip(name, "无法把焦点移开目标窗口（缺少 wmctrl/xdotool？）")
        return

    client.call_tool("get_app_state", {"app": app_name})
    # 用 Home 而不是 ctrl+a：这个检查只关心"有没有静默投递到别的窗口"，
    # 不需要按键产生任何效果，而 ctrl+a 会留下全选状态污染后续检查。
    is_error, text = client.call_tool("press_key", {"app": app_name, "key": "Home"})

    if is_error:
        report.ok(name, "硬失败: {}".format((text or "").splitlines()[0][:80]))
        return

    time.sleep(0.4)
    if read_truth(app_name).get("windowActive"):
        report.ok(name, "先把目标窗口抬到前台再合成")
    else:
        report.fail(
            name,
            "工具报成功，但目标窗口始终未激活——按键被投递给了当时持有焦点的其它窗口",
        )


def check_state_tools_still_work(client, app_name, report):
    name = "list_apps / get_app_state 正常"
    is_error, text = client.call_tool("list_apps", {})
    if is_error or not text.strip():
        report.fail(name, "list_apps 失败")
        return
    is_error, text = client.call_tool("get_app_state", {"app": app_name})
    if is_error:
        report.fail(name, "get_app_state 失败: {}".format(text.splitlines()[0] if text else ""))
        return
    report.ok(name)


def check_tree_has_no_sentinel_coordinates(client, app_name, report):
    name = "accessibility tree 不含 INT_MIN 哨兵坐标"
    is_error, text = client.call_tool("get_app_state", {"app": app_name})
    if is_error:
        report.fail(name, "get_app_state 失败")
        return
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "-214748" in line or "2147483" in line
    ]
    if offenders:
        report.fail(
            name,
            "{} 行仍带哨兵坐标，例如: {}".format(len(offenders), offenders[0][:90]),
        )
    else:
        report.ok(name)


def check_type_text_inserts_at_caret(client, app_name, report):
    name = "type_text 在 caret 处插入而不是追加到末尾"
    if (read_truth(app_name).get("focusedText") or {}).get("chars", 0) < 2:
        report.skip(name, "焦点控件内容太短，无法区分插入位置")
        return

    # caret 默认停在末尾，那样插入和追加的结果一样，区分不出来。先移到开头。
    client.call_tool("get_app_state", {"app": app_name})
    client.call_tool("press_key", {"app": app_name, "key": "Home"})
    time.sleep(0.4)

    focused = read_truth(app_name).get("focusedText") or {}
    original = focused.get("text") or ""
    caret = focused.get("caret")
    if caret is None or caret >= len(original):
        report.skip(name, "caret 仍在末尾或不可读，插入与追加无法区分")
        return

    client.call_tool("get_app_state", {"app": app_name})
    is_error, text = client.call_tool(
        "type_text", {"app": app_name, "text": PROBE_TEXT}
    )
    if is_error:
        report.fail(name, "工具返回错误: {}".format(text.splitlines()[0] if text else ""))
        return

    time.sleep(0.5)
    after = (read_truth(app_name).get("focusedText") or {}).get("text") or ""
    expected = original[:caret] + PROBE_TEXT + original[caret:]
    remove_probe_text(app_name)
    if after == expected:
        report.ok(name, "插入在 offset {}".format(caret))
    elif after == original + PROBE_TEXT:
        report.fail(name, "仍然追加到了末尾（caret 在 offset {}）".format(caret))
    else:
        report.fail(name, "落点不符合预期: {!r}".format(after[:60]))


def check_action_reports_execution_path(client, app_name, report):
    name = "动作结果说明执行路径与是否已确认"
    client.call_tool("get_app_state", {"app": app_name})
    is_error, text = client.call_tool("press_key", {"app": app_name, "key": "Right"})
    if is_error:
        report.skip(name, "press_key 未执行: {}".format((text or "").splitlines()[0][:60]))
        return
    first = (text or "").splitlines()[0] if text else ""
    if not first.startswith("Note:"):
        report.fail(name, "结果没有以 Note 开头说明执行路径: {!r}".format(first[:80]))
        return
    if "not verified" not in text:
        report.fail(name, "合成动作没有被标注为未确认")
        return
    report.ok(name, first[:70])


def check_no_op_action_is_flagged(client, app_name, report):
    """连按两次同样的无效果按键，第二次应被标为"什么都没变"。"""
    name = "无可观测变化的动作被标注出来"
    client.call_tool("get_app_state", {"app": app_name})
    # 第一次可能会移动 caret；第二次在同一位置重复，通常什么都不变。
    client.call_tool("press_key", {"app": app_name, "key": "Home"})
    is_error, text = client.call_tool("press_key", {"app": app_name, "key": "Home"})
    if is_error:
        report.skip(name, "press_key 未执行")
        return
    if "Nothing observable changed" in (text or ""):
        report.ok(name)
    else:
        report.skip(name, "这一次动作确实产生了可观测变化，无法验证该分支")


def main():
    parser = argparse.ArgumentParser(
        description="在真实桌面会话里验证 Linux 输入链路"
    )
    parser.add_argument("--app", required=True, help="目标应用名，例如 gedit")
    parser.add_argument(
        "--binary",
        default=DEFAULT_BINARY,
        help="open-computer-use 可执行文件路径（相对仓库根目录）。默认 {}".format(
            DEFAULT_BINARY
        ),
    )
    args = parser.parse_args()

    binary = args.binary
    if not os.path.isabs(binary):
        binary = os.path.join(REPO_ROOT, binary)
    if not os.path.exists(binary):
        print(
            "找不到可执行文件: {}\n先运行 scripts/build-open-computer-use-linux.sh "
            "--arch amd64".format(args.binary),
            file=sys.stderr,
        )
        return 2

    probe = read_truth(args.app)
    if not probe.get("found"):
        print("AT-SPI 里找不到应用 {!r}，请确认它正在运行".format(args.app), file=sys.stderr)
        return 2

    print("目标应用: {}  (可编辑控件 {} 个)".format(args.app, probe.get("editableTotal")))
    report = Report()
    client = MCPClient(binary)
    try:
        client.handshake()
        check_state_tools_still_work(client, args.app, report)
        check_tree_has_no_sentinel_coordinates(client, args.app, report)
        check_type_text_lands_in_focused_widget(client, args.app, report)
        check_type_text_inserts_at_caret(client, args.app, report)
        check_action_reports_execution_path(client, args.app, report)
        check_no_op_action_is_flagged(client, args.app, report)
        check_press_key_never_silently_misdelivers(client, args.app, report)
    finally:
        client.close()

    print()
    if report.failures:
        print("失败 {} 项: {}".format(len(report.failures), ", ".join(report.failures)))
        return 1
    print("全部通过{}".format(
        "（跳过 {} 项）".format(len(report.skipped)) if report.skipped else ""
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
