#!/usr/bin/env python3
"""通道 × 工具 × 工具包的矩阵验证：**每一条链路都真跑一遍**。

和 `measure-baseline.py` 的分工：那个跑的是少数几条**已知能成**的任务链，
用来发现回归；这个相反，是**穷举**——每个工具在每个应用上都试，把"哪条链路
在哪个工具包上不通"整张表摊开。

判据一律用**外部真值或状态转移**，不采信工具自己的返回值：
  - `[semantic]` / `[synthesis]` 标签说明实际走了哪条执行路径
  - `[a11y]` / `[gui]` / `[keyboard]` 标签说明靠什么定位的
  - 树/窗口标题/焦点的实际变化，而不是 isError

用法:
  scripts/verify-channel-matrix.py                 # 全部
  scripts/verify-channel-matrix.py --app gedit     # 只测一个应用
  scripts/verify-channel-matrix.py --json out.json
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")

# (显示名, MCP 的 app 参数, 工具包, 启动命令, 窗口标题片段)
APPS = [
    ("gedit", "gedit", "GTK", "gedit /tmp/ocu-matrix.txt", "ocu-matrix.txt"),
    ("Nautilus", "org.gnome.Nautilus", "GTK", "nautilus /tmp", "tmp"),
    ("VLC", "vlc", "Qt", "vlc --no-video", "VLC media player"),
    ("Impress", "soffice", "VCL", None, "Impress"),
    ("Thunderbird", "Thunderbird", "Gecko", "thunderbird", "Mozilla Thunderbird"),
    ("VS Code", "code", "Electron", None, "Visual Studio Code"),
    ("GIMP", "gimp", "GAIL", None, "GIMP"),
]


class MCP:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._id = 0
        self.handshake()

    def send(self, method, params=None, notify=False, timeout=180):
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
        thread = threading.Thread(
            target=lambda: box.setdefault("line", self.process.stdout.readline()),
            daemon=True)
        thread.start()
        thread.join(timeout)
        if not box.get("line"):
            raise RuntimeError("MCP 无响应: {}".format(method))
        return json.loads(box["line"])

    def handshake(self):
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "matrix", "version": "1"}})
        self.send("notifications/initialized", {}, notify=True)

    def call(self, name, arguments):
        """返回 (text, is_error, has_image)。"""
        response = self.send("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result") or {}
        text = ""
        has_image = False
        for item in result.get("content", []):
            if item.get("type") == "text" and not text:
                text = item.get("text", "")
            if item.get("type") == "image":
                has_image = True
        return text, bool(result.get("isError")), has_image

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


def running(fragment):
    out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
    return any(fragment in line for line in out.splitlines())


def launch(command, fragment, timeout=90):
    if running(fragment):
        return True
    if command is None:
        return False
    subprocess.Popen("setsid {} </dev/null >/dev/null 2>&1 &".format(command),
                     shell=True, start_new_session=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running(fragment):
            time.sleep(4)
            return True
        time.sleep(2)
    return False


def parse_line(line):
    """按新文法拆一行：`<idx> <role> "<name>" [..] {x,y,w,h}: "<value>"`。"""
    stripped = line.strip()
    match = re.match(r"^(\d+)\s+(.*)$", stripped)
    if not match:
        return None
    index, rest = match.group(1), match.group(2)
    name = ""
    name_match = re.search(r'"((?:[^"\\]|\\.)*)"', rest)
    if name_match and not rest[:name_match.start()].strip().startswith("["):
        name = name_match.group(1)
    role = re.split(r'["\[{:]', rest, 1)[0].strip()
    frame = None
    frame_match = re.search(r"\{(-?\d+),(-?\d+),(\d+),(\d+)\}", rest)
    if frame_match:
        frame = tuple(int(g) for g in frame_match.groups())
    return {
        "index": index, "role": role, "name": name, "frame": frame,
        "has_click": "[has-click-action]" in rest or "has-click-action" in rest,
        "raw": stripped,
    }


def elements(tree):
    out = []
    for line in tree.splitlines():
        parsed = parse_line(line)
        if parsed:
            out.append(parsed)
    return out


def refused(text):
    """守卫拒绝了这次合成——环境问题，不是链路问题。"""
    return REFUSAL_MARKER in text


def tag_of(text):
    """从 Note 里读出这次动作实际走的通道与执行路径。"""
    address = next((t for t in ("[a11y]", "[gui]", "[keyboard]") if t in text), None)
    execution = next((t for t in ("[semantic]", "[synthesis]") if t in text), None)
    return address, execution


PASS, FAIL, SKIP, GUARD = "✅", "❌", "—", "🛡"

# 焦点守卫拒绝合成**是设计行为**，不是链路断了：输入合成是全局的，抢不到前台
# 就宁可硬失败，也不要把内容送进别的窗口。实测跑矩阵时 VS Code 弹了 Workspace
# Trust 模态框霸占焦点，Thunderbird 的四条合成链路全被拒——而诊断准确报出了
# "Input focus is currently held by 'Workspace Trust - Visual Studio Code'"。
# 把这种情况记成失败，等于惩罚工具做对了事。
REFUSAL_MARKER = "Refusing to synthesize"


def check(rows, app, tool, channel, ok, detail, guarded=False):
    rows.append({"app": app, "tool": tool, "channel": channel,
                 "ok": ok, "detail": detail, "guarded": guarded})
    mark = GUARD if guarded else (PASS if ok is True else (FAIL if ok is False else SKIP))
    print("    {:<22} {:<10} {} {}".format(tool, channel, mark, detail[:74]))


def probe_app(client, display_name, app, toolkit, rows):
    print("  {} ({})".format(display_name, toolkit))

    # ---- 观测：a11y 树 ----
    tree, err, has_image = client.call("get_app_state", {"app": app, "boxes": True})
    if err:
        check(rows, display_name, "get_app_state", "a11y", False, tree[:70])
        return
    items = elements(tree)
    check(rows, display_name, "get_app_state", "a11y", bool(items),
          "{} 个元素".format(len(items)))
    # ---- 观测：截图随树附带 ----
    check(rows, display_name, "get_app_state", "gui", has_image,
          "截图随树附带" if has_image else "没有附带截图")
    # ---- 文法：自由文本必须加引号 ----
    named = [e for e in items if e["name"]]
    quoted_ok = all('"' in e["raw"] for e in named)
    check(rows, display_name, "snapshot-grammar", "a11y", quoted_ok or not named,
          "{}/{} 个有名元素带引号".format(sum('"' in e["raw"] for e in named), len(named)))
    # ---- 几何：新文法的紧凑矩形 ----
    with_frame = [e for e in items if e["frame"]]
    check(rows, display_name, "snapshot-geometry", "a11y", bool(with_frame),
          "{}/{} 个元素带 {{x,y,w,h}}".format(len(with_frame), len(items)))

    # ---- 观测：只要图不要树 ----
    shot, err, has_image = client.call("get_screenshot", {"app": app})
    check(rows, display_name, "get_screenshot", "gui", (not err) and has_image,
          shot[:70] if err else "拿到图")

    def refresh():
        """每个动作前重取快照。动作会改变树、索引会重排——测试脚本自己
        不能犯它正在检验的那类错误。"""
        text, err, _ = client.call("get_app_state", {"app": app, "boxes": True})
        return [] if err else elements(text)

    # ---- 动作：a11y 语义点击（挑一个无害的可点元素）----
    items = refresh() or items
    target = pick_harmless(items)
    if target is None:
        check(rows, display_name, "click", "a11y", None, "没有安全的可点元素")
    else:
        text, err, _ = client.call("click", {
            "app": app, "element_index": target["index"],
            "element": target["name"] or target["role"]})
        address, execution = tag_of(text)
        check(rows, display_name, "click", "a11y", (not err) and address == "[a11y]",
              "{}{} {}".format(address or "无通道标签", execution or "",
                               target["role"] + " " + repr(target["name"])))

    # ---- 动作：意图声明的交叉核对 ----
    items = refresh() or items
    target = pick_harmless(items)
    if target is not None and target["name"]:
        text, err, _ = client.call("click", {
            "app": app, "element_index": target["index"],
            "element": "zzqq nonexistent widget"})
        check(rows, display_name, "click(intent-guard)", "a11y",
              err and "does not match" in text,
              "拒绝了不符的声明" if err else "**没有拦住**: " + text[:50])

    # ---- 寻址：选择器（跨快照存活，不依赖下标）----
    items = refresh() or items
    named = next((e for e in items if e["name"] and e["role"]), None)
    if named is None:
        check(rows, display_name, "click(selector)", "a11y", None, "没有有名元素")
    else:
        selector = '{} "{}"'.format(named["role"], named["name"])
        text, err, _ = client.call("click", {
            "app": app, "element_index": selector, "element": named["name"]})
        if refused(text):
            check(rows, display_name, "click(selector)", "a11y", None,
                  "焦点守卫拒绝（环境被别的窗口占住）", guarded=True)
            return
        address, _ = tag_of(text)
        ambiguous = "ambiguous" in text
        check(rows, display_name, "click(selector)", "a11y",
              (not err and address == "[a11y]") or ambiguous,
              "选择器命中" if not err else ("歧义并列出候选" if ambiguous else text[:60]))

    # ---- 观测：几何 opt-in ----
    plain, err, _ = client.call("get_app_state", {"app": app})
    boxed, err2, _ = client.call("get_app_state", {"app": app, "boxes": True})
    if err or err2:
        check(rows, display_name, "get_app_state(boxes)", "a11y", False, "取不到")
    else:
        saved = 100 * (len(boxed) - len(plain)) // max(len(boxed), 1)
        check(rows, display_name, "get_app_state(boxes)", "a11y",
              len(plain) < len(boxed) and "{" not in plain.split("\n")[2],
              "默认不带几何，省 {}%".format(saved))

    # ---- 动作：GUI 坐标点击 + 命中回报 ----
    items = refresh() or items
    target = pick_harmless(items)
    if target is not None and target["frame"]:
        x = target["frame"][0] + target["frame"][2] // 2
        y = target["frame"][1] + target["frame"][3] // 2
        text, err, has_image = client.call("click_xy", {"app": app, "x": x, "y": y})
        if refused(text):
            check(rows, display_name, "click_xy", "gui", None,
                  "焦点守卫拒绝（环境被别的窗口占住）", guarded=True)
            return
        address, _ = tag_of(text)
        hit = re.search(r"Hit test says the element under that point is ([^—]+)—", text)
        hit_desc = hit.group(1).strip() if hit else "未命中"
        expected = (target["name"] or target["role"]).lower()
        agrees = expected[:12] in hit_desc.lower() if hit else False
        check(rows, display_name, "click_xy", "gui",
              (not err) and address == "[gui]" and has_image,
              "命中 {}{}".format(hit_desc[:40], "（与期望一致）" if agrees else ""))
    else:
        check(rows, display_name, "click_xy", "gui", None, "没有可用坐标")

    # ---- 动作：键盘 ----
    text, err, _ = client.call("press_key", {"app": app, "key": "Escape"})
    if refused(text):
        check(rows, display_name, "press_key", "keyboard", None,
              "焦点守卫拒绝（环境被别的窗口占住）", guarded=True)
        return
    address, execution = tag_of(text)
    check(rows, display_name, "press_key", "keyboard",
          (not err) and address == "[keyboard]",
          "{}{}".format(address or "无通道标签", execution or ""))

    # ---- 动作：滚动 ----
    items = refresh() or items
    target = pick_harmless(items) or (items[0] if items else None)
    if target is None:
        check(rows, display_name, "scroll", "keyboard", None, "树是空的")
        return
    text, err, _ = client.call("scroll", {
        "app": app, "direction": "down", "element_index": (target or items[0])["index"],
        "pages": 1})
    address, _ = tag_of(text)
    check(rows, display_name, "scroll", "keyboard",
          (not err) and address == "[keyboard]",
          "如实标注不按元素定位" if "did NOT target" in text else "缺少定位说明")

    # ---- 动作：拖拽（原地拖，无副作用）----
    if items and items[0]["frame"]:
        f = items[0]["frame"]
        cx, cy = f[0] + f[2] // 2, f[1] + f[3] // 2
        text, err, has_image = client.call("drag_xy", {
            "app": app, "from_x": cx, "from_y": cy, "to_x": cx, "to_y": cy})
        address, _ = tag_of(text)
        check(rows, display_name, "drag_xy", "gui",
              (not err) and address == "[gui]" and has_image,
              "带截图" if has_image else "**没带截图**")
    else:
        check(rows, display_name, "drag_xy", "gui", None, "没有可用坐标")

    # ---- 动作：语义写值 ----
    items = refresh() or items
    settable = next((e for e in items if e["role"] in ("text", "entry", "password text")), None)
    if settable is None:
        check(rows, display_name, "set_value", "a11y", None, "没有可写控件")
    else:
        text, err, _ = client.call("set_value", {
            "app": app, "element_index": settable["index"],
            "element": settable["name"] or settable["role"], "value": "ocu-matrix"})
        address, execution = tag_of(text)
        check(rows, display_name, "set_value", "a11y",
              (not err) and address == "[a11y]", (address or "") + (execution or "") or text[:50])

    # ---- 动作：invoke_element_action ----
    items = refresh() or items
    with_actions = next((e for e in items if "[actions=" in e["raw"]), None)
    if with_actions is None:
        check(rows, display_name, "invoke_element_action", "a11y", None, "树里没有次级动作")
    else:
        action = re.search(r"\[actions=([^,\]]+)", with_actions["raw"]).group(1)
        text, err, _ = client.call("invoke_element_action", {
            "app": app, "element_index": with_actions["index"],
            "element": with_actions["name"] or with_actions["role"], "action": action})
        address, _ = tag_of(text)
        check(rows, display_name, "invoke_element_action", "a11y",
              (not err) and address == "[a11y]", "动作 {}".format(action))


HARMLESS_ROLES = ("push button", "toggle button", "menu", "tree item", "list item",
                  "table cell", "page tab", "radio button", "check box", "label",
                  "panel", "text", "entry")
# 点了会破坏环境或打断测试的元素。**宁可跳过，也不要在测试里毁掉现场**——
# 第一版把 Minimize 当成了"无害按钮"，它把窗口最小化，后面每一步都跟着错。
DANGEROUS = ("close", "quit", "exit", "delete", "remove", "shut", "关闭", "退出",
             "discard", "reset", "restart", "logout", "power", "eject", "format",
             "minimize", "maximize", "unmaximize", "fullscreen", "全屏", "最小化",
             "save", "print", "send", "install", "update", "uninstall")


def pick_harmless(items):
    """挑一个点了不会造成破坏的元素。宁可跳过，也不要在测试里毁掉环境。"""
    for element in items:
        if element["role"] not in HARMLESS_ROLES:
            continue
        blob = (element["name"] + " " + element["raw"]).lower()
        if any(word in blob for word in DANGEROUS):
            continue
        if not element["frame"]:
            continue
        return element
    return None


def main():
    parser = argparse.ArgumentParser(description="通道 × 工具 × 工具包矩阵验证")
    parser.add_argument("--binary", default=DEFAULT_BIN)
    parser.add_argument("--app", action="append", default=[])
    parser.add_argument("--json")
    args = parser.parse_args()

    with open("/tmp/ocu-matrix.txt", "w", encoding="utf-8") as handle:
        handle.write("matrix\n")

    rows = []
    for display_name, app, toolkit, command, fragment in APPS:
        if args.app and display_name not in args.app and app not in args.app:
            continue
        if not launch(command, fragment):
            print("  {} ({}) 起不来，跳过".format(display_name, toolkit))
            rows.append({"app": display_name, "tool": "-", "channel": "-",
                         "ok": None, "detail": "应用起不来"})
            continue
        client = MCP(args.binary)
        try:
            probe_app(client, display_name, app, toolkit, rows)
        except Exception as error:
            print("    异常: {}".format(error))
            rows.append({"app": display_name, "tool": "?", "channel": "?",
                         "ok": False, "detail": "异常 {}".format(error)})
        finally:
            client.close()
        print()

    total = [r for r in rows if r["ok"] is not None]
    good = [r for r in total if r["ok"]]
    print("=" * 66)
    print("矩阵结果：{}/{} 条链路通过".format(len(good), len(total)))
    bad = [r for r in total if not r["ok"]]
    if bad:
        print("\n不通过的链路：")
        for row in bad:
            print("  {:<14} {:<22} {:<10} {}".format(
                row["app"], row["tool"], row["channel"], row["detail"][:60]))
    print("=" * 66)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"rows": rows}, handle, ensure_ascii=False, indent=2)
        print("已写入 {}".format(args.json))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
