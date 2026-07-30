#!/usr/bin/env python3
"""录制操作轨迹，供裁剪方案的保留率评测使用（待办 #8）。

裁剪的核心风险是"砍掉了任务真正需要的元素而不自知"。判断一个裁剪方案好不好，
需要两个数：

- **保留率**：被实际操作过的元素，裁剪后有多少仍然存在且可寻址
- **压缩率**：裁剪后 token 相对原始的比例

这个脚本负责产出前者所需的原料：把一段已知可完成的操作序列跑一遍，
逐步记录**当时的完整树**与**这一步实际操作的元素**。

元素身份不能用 `element_index`——实测表明它是位置性的，树结构一变就永久重排
（gedit 上菜单开合会让 26% 的索引指向不同元素，且关掉菜单也不回弹）。
所以每步额外记录目标元素的**完整渲染行**作为稳定标识：它带 role、name、
状态标记和 Frame 坐标，足以在裁剪后的树里把同一个元素重新认出来。
这也正是 agent 实际定位元素的方式，比内部 id 更贴近评测要回答的问题。

用法:
  scripts/record-trajectory.py --scenario gedit-type --out /tmp/traj.jsonl
  scripts/record-trajectory.py --list

输出为 JSON Lines，每行一步：
  {"step": 0,
   "action": {"tool": ..., "arguments": {...}},
   "target": {"index": 16, "描述": "toggle button Menu", "identity": "16 toggle button Menu Frame: {...}"},
   "notes": ["Note: [semantic] ..."],
   "tree": {"lines": [...], "elements": {...}, "raw": {...}}}

`notes` 里的 `[semantic]` / `[synthesis]` 标签让轨迹同时可用于统计
"语义调用 vs 坐标兜底"的比例（plan 中 S3 报告口径的第四项）。
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")


class MCP:
    """最小 stdio JSON-RPC 客户端。"""

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

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self._id += 1
            message["id"] = self._id
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        if notify:
            return None
        box = {}

        def read():
            box["line"] = self.process.stdout.readline()

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(120)
        if not box.get("line"):
            raise RuntimeError("MCP 无响应: {}".format(method))
        return json.loads(box["line"])

    def handshake(self):
        self.send("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "trajectory-recorder", "version": "1"},
        })
        self.send("notifications/initialized", {}, notify=True)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


def tool_text(response):
    result = response.get("result", {})
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", ""), bool(result.get("isError"))
    return "", bool(result.get("isError"))


def parse_tree(text):
    """把渲染的树拆成 (index -> "role name")、原始行、以及 index -> 完整行。

    完整行是保留率评测用的稳定标识：它带 role、name、状态标记和 Frame 坐标，
    足以在裁剪后的树里把同一个元素认出来。只用 "role name" 对无名控件不够区分
    （对话框里大量 `combo box` / `toggle button` 都没有名字）。
    """
    elements = {}
    raw = {}
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit():
            continue
        lines.append(line)
        index, _, rest = stripped.partition(" ")
        # 新文法：附加属性一律进方括号、几何是 {x,y,w,h}、值在末尾冒号之后。
        for marker in (" [", " {", ": "):
            position = rest.find(marker)
            if position >= 0:
                rest = rest[:position]
        try:
            key = int(index)
        except ValueError:
            continue
        elements[key] = rest.strip()
        raw[key] = stripped
    return elements, lines, raw


def find_index(elements, role, name):
    """按 role + 精确名定位。子串匹配会点错元素——实测 `Format` 会同时命中
    `menu Format`、`check menu item Formatting Marks` 等四个节点。"""
    for index, body in sorted(elements.items()):
        if body == "{} {}".format(role, name):
            return index
    return None


# 场景是脚本化的已知可完成序列，不需要 LLM。每步给出 (工具, 参数构造器)。
# 参数构造器接收当前 elements，返回 arguments 或 None（表示这步跳过）。
SCENARIOS = {
    "gedit-type": {
        "app": "gedit",
        "desc": "在 gedit 里定位光标并输入文本",
        "steps": [
            ("press_key", lambda e: {"key": "ctrl+a"}),
            ("press_key", lambda e: {"key": "Home"}),
            ("type_text", lambda e: {"text": "trajectory step"}),
            ("press_key", lambda e: {"key": "End"}),
        ],
    },
    "gedit-menu": {
        "app": "gedit",
        "desc": "打开 gedit 的主菜单并关闭",
        "steps": [
            ("click", lambda e: _by(e, "toggle button", "Menu")),
            ("press_key", lambda e: {"key": "Escape"}),
        ],
    },
    "writer-line-spacing": {
        "app": "soffice",
        "desc": "LibreOffice Writer：格式 → 段落 → 行距下拉 → 选项 → 确定",
        "steps": [
            ("press_key", lambda e: {"key": "ctrl+a"}),
            ("click", lambda e: _by(e, "menu", "Format")),
            ("click", lambda e: _by(e, "menu item", "Paragraph...")),
            # 行距 combo 的真实控件是 panel Line Spacing 下的 toggle button，
            # 那个 combo box 节点是 INT_MIN 幻影（见 plan 的实测发现）
            ("click", lambda e: _under(e, "panel Line Spacing", "toggle button")),
            # 下拉里的单元格必须走坐标点击：do_action 会关掉下拉但不提交值。
            # 这条路能走通的前提是单元格带 Frame（曾经缺失，已修）。
            ("click", lambda e: _endswith(e, "Double", method="global")),
            ("click", lambda e: _by(e, "push button", "OK")),
        ],
    },
    "gedit-toolbar": {
        "app": "gedit",
        "desc": "gedit：依次点击工具栏上的几个按钮（全部元素定向）",
        "steps": [
            ("click", lambda e: _by(e, "toggle button", "Open")),
            ("press_key", lambda e: {"key": "Escape"}),
            ("click", lambda e: _by(e, "toggle button", "Menu")),
            ("press_key", lambda e: {"key": "Escape"}),
            ("click", lambda e: _by(e, "push button", "New")),
        ],
    },
    "nautilus-browse": {
        "app": "org.gnome.Nautilus",
        "desc": "Nautilus：元素定向点击侧边栏与工具栏",
        "steps": [
            ("click", lambda e: _by(e, "push button", "Search")),
            ("press_key", lambda e: {"key": "Escape"}),
            ("click", lambda e: _by(e, "toggle button", "View options")),
            ("press_key", lambda e: {"key": "Escape"}),
        ],
    },
}


def _by(elements, role, name):
    index = find_index(elements, role, name)
    if index is None:
        return None
    return {"element_index": str(index), "click_method": "accessibility"}


def _endswith(elements, suffix, method="accessibility"):
    """按名字后缀定位，用于下拉里的 `table cell R3C0 Double` 这类渲染。"""
    for index, body in sorted(elements.items()):
        if body.endswith(suffix):
            return {"element_index": str(index), "click_method": method}
    return None


def _under(elements, container, role):
    """定位某个命名容器之后出现的第一个指定角色节点。

    对话框里大量控件没有名字（行距 combo 的 toggle button 就是），
    只能靠"在哪个命名面板下面"来指认——这也是 agent 面临的真实消歧成本。
    """
    started = False
    for index, body in sorted(elements.items()):
        if body == container:
            started = True
            continue
        if started and body.strip() == role:
            return {"element_index": str(index), "click_method": "accessibility"}
    return None


def record(scenario_name, binary, out_path):
    scenario = SCENARIOS[scenario_name]
    app = scenario["app"]
    client = MCP(binary)
    written = 0
    try:
        client.handshake()
        with open(out_path, "w", encoding="utf-8") as handle:
            for step, (tool, build) in enumerate(scenario["steps"]):
                response = client.send("tools/call", {
                    "name": "get_app_state",
                    "arguments": {"app": app, "max_tree_nodes": 1500},
                })
                text, is_error = tool_text(response)
                if is_error:
                    print("  step {}: 取状态失败 {}".format(step, text[:70]), file=sys.stderr)
                    break
                elements, lines, raw = parse_tree(text)

                arguments = build(elements)
                if arguments is None:
                    print("  step {}: {} 找不到目标，跳过".format(step, tool), file=sys.stderr)
                    continue

                target = None
                if "element_index" in arguments:
                    index = int(arguments["element_index"])
                    target = {
                        "index": index,
                        "描述": elements.get(index, ""),
                        # 稳定标识：完整渲染行，带 role/name/状态/Frame，
                        # 用于在裁剪后的树里把同一元素认出来
                        "identity": raw.get(index, ""),
                    }

                call = client.send("tools/call", {
                    "name": tool, "arguments": dict(arguments, app=app),
                })
                note_text, action_error = tool_text(call)
                notes = [l for l in note_text.splitlines() if l.startswith("Note:")]

                handle.write(json.dumps({
                    "step": step,
                    "scenario": scenario_name,
                    "app": app,
                    "action": {"tool": tool, "arguments": arguments},
                    "target": target,
                    "isError": action_error,
                    "notes": notes,
                    "tree": {"lines": lines, "elements": elements, "raw": raw},
                }, ensure_ascii=False) + "\n")
                written += 1
                print("  step {}: {} -> isError={} target={}".format(
                    step, tool, action_error, (target or {}).get("描述", "-")[:40]))
                time.sleep(1.2)
    finally:
        client.close()
    return written


def main():
    parser = argparse.ArgumentParser(description="录制操作轨迹供保留率评测使用")
    parser.add_argument("--scenario", help="场景名，见 --list")
    parser.add_argument("--out", default="/tmp/trajectory.jsonl", help="输出文件")
    parser.add_argument("--binary", default=DEFAULT_BIN, help="open-computer-use 路径")
    parser.add_argument("--list", action="store_true", help="列出可用场景")
    args = parser.parse_args()

    if args.list or not args.scenario:
        print("可用场景：")
        for name, scenario in SCENARIOS.items():
            print("  {:24} {}  (应用: {})".format(name, scenario["desc"], scenario["app"]))
        return 0 if args.list else 2

    if args.scenario not in SCENARIOS:
        print("未知场景 {!r}，用 --list 查看".format(args.scenario), file=sys.stderr)
        return 2
    if not os.path.exists(args.binary):
        print("找不到可执行文件 {}".format(args.binary), file=sys.stderr)
        return 2

    print("录制场景 {} (应用 {})".format(args.scenario, SCENARIOS[args.scenario]["app"]))
    written = record(args.scenario, args.binary, args.out)
    print("\n已写入 {} 步到 {}".format(written, args.out))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
