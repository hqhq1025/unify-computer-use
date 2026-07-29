#!/usr/bin/env python3
"""a11y readiness probe：逐个应用量出 a11y-first 是否可行、以及观测要花多少 token。

同时量两层：
  1. 原始 AT-SPI 树 —— 这个应用到底暴露了多少东西
  2. MCP get_app_state 的实际输出 —— agent 真正要吃进去的 token

判据不是"有没有树"，而是"树里有多少可交互、可定位的节点"。一个 5000 节点
但只有 3 个 actionable 的树，对 agent 来说和没有树是一样的。

用法:
  scripts/a11y-readiness-probe.py                    # 全部应用
  scripts/a11y-readiness-probe.py gedit GIMP         # 指定应用
  OCU_BIN=dist/linux/amd64/open-computer-use scripts/a11y-readiness-probe.py

注意:
  - 会依次启动被测应用并在测完后关掉，期间焦点会被反复抢占
  - 需要真实桌面会话（X11/Wayland + a11y 总线）
  - 结果写到 /tmp/a11y-probe-result.json

两个已知的测量陷阱（踩过，别再踩）:
  1. 僵尸 AT-SPI 注册：应用被 kill 后，AT-SPI 里仍残留 app+frame 节点。
     直接按名字查会量到一个 2 节点的空壳，误判成"无 a11y"。
     本脚本用启动前后 diff 桌面列表来发现应用，规避这一点。
  2. 浏览器会话交接：带新参数启动 Chrome 时，若已有实例在跑，新调用会被
     交接过去、参数完全失效。测 Chrome 必须先彻底杀干净，并用独立
     --user-data-dir。
"""
import json
import os
import subprocess
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCU = os.environ.get(
    "OCU_BIN", os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")
)
NODE_CAP = 4000
DEPTH_CAP = 40
WALK_BUDGET = 25.0


def safe(call, default=None):
    try:
        value = call()
        return default if value is None else value
    except Exception:
        return default


def desktop_app(atspi_name):
    d = Atspi.get_desktop(0)
    for i in range(safe(d.get_child_count, 0) or 0):
        a = safe(lambda i=i: d.get_child_at_index(i))
        if a is not None and safe(a.get_name, "") == atspi_name:
            return a
    return None


def raw_walk(app):
    """原始遍历。尊重 MANAGES_DESCENDANTS，否则会掉进 Calc 那种谎报 21 亿子节点的容器。"""
    stats = {
        "nodes": 0,
        "depth": 0,
        "visible": 0,
        "actionable": 0,
        "named": 0,
        "editable": 0,
        "managed": 0,
        "truncated": False,
        "timeout": False,
    }
    deadline = time.time() + WALK_BUDGET

    def visit(node, depth):
        if stats["nodes"] >= NODE_CAP:
            stats["truncated"] = True
            return
        if time.time() > deadline:
            stats["timeout"] = True
            return
        if depth > DEPTH_CAP:
            return
        stats["nodes"] += 1
        stats["depth"] = max(stats["depth"], depth)

        if safe(node.get_name, ""):
            stats["named"] += 1
        if int(safe(node.get_n_actions, 0) or 0) > 0:
            stats["actionable"] += 1

        ss = safe(node.get_state_set)
        if ss is not None:
            if safe(lambda: ss.contains(Atspi.StateType.EDITABLE), False) and (
                safe(node.get_editable_text_iface) is not None
            ):
                stats["editable"] += 1
            if safe(lambda: ss.contains(Atspi.StateType.MANAGES_DESCENDANTS), False):
                stats["managed"] += 1
                return  # 不进去，这是超大容器的正式契约

        comp = safe(node.get_component_iface)
        if comp is not None:
            rect = safe(
                lambda: Atspi.Component.get_extents(comp, Atspi.CoordType.SCREEN)
            )
            if (
                rect is not None
                and rect.width > 0
                and rect.height > 0
                and abs(rect.x) < 100000
                and abs(rect.y) < 100000
            ):
                stats["visible"] += 1

        for i in range(safe(node.get_child_count, 0) or 0):
            c = safe(lambda i=i: node.get_child_at_index(i))
            if c is not None:
                visit(c, depth + 1)

    t0 = time.time()
    visit(app, 0)
    stats["walk_ms"] = int((time.time() - t0) * 1000)
    return stats


def mcp_snapshot(atspi_name, quota=1200):
    t0 = time.time()
    try:
        p = subprocess.run(
            [OCU, "call", "get_app_state", "--args",
             json.dumps({"app": atspi_name, "max_tree_nodes": quota})],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"ms": 120000, "lines": 0, "chars": 0, "error": "timeout"}
    ms = int((time.time() - t0) * 1000)
    try:
        data = json.loads(p.stdout)
        text = ""
        has_image = False
        for c in data.get("content", []):
            if c.get("type") == "text":
                text = c.get("text", "")
            if c.get("type") == "image":
                has_image = True
        if data.get("isError"):
            return {"ms": ms, "lines": 0, "chars": 0, "error": text[:70]}
        lines = [l for l in text.splitlines() if l.strip()]
        return {
            "ms": ms,
            "lines": len(lines),
            "chars": len(text),
            "screenshot": has_image,
            "error": None,
        }
    except Exception as e:
        return {"ms": ms, "lines": 0, "chars": 0, "error": "parse: {}".format(e)[:70]}


def desktop_names():
    d = Atspi.get_desktop(0)
    names = []
    for i in range(safe(d.get_child_count, 0) or 0):
        a = safe(lambda i=i: d.get_child_at_index(i))
        if a is not None:
            n = safe(a.get_name, "")
            if n:
                names.append(n)
    return names


def wait_for_new_app(before, timeout=60):
    """启动后靠 diff 桌面列表发现应用，不依赖猜 AT-SPI 注册名。

    应用的 AT-SPI 名常常和进程名对不上（Chrome -> 'Google Chrome'，
    Nautilus -> 'org.gnome.Nautilus' 之类），猜名字会得到假的"无 a11y"结论。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = [n for n in desktop_names() if n not in before]
        if new:
            time.sleep(3)  # 让 UI 稳定下来再量
            # 再 diff 一次，取最终稳定的名字
            new = [n for n in desktop_names() if n not in before] or new
            return new[0]
        time.sleep(1.5)
    return None


def wait_for(atspi_name, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if desktop_app(atspi_name) is not None:
            time.sleep(3)  # 让 UI 稳定下来再量
            return True
        time.sleep(1.5)
    return False


def launch(cmd):
    subprocess.Popen(
        "setsid {} </dev/null >/dev/null 2>&1 &".format(cmd),
        shell=True, start_new_session=True,
    )


def probe(spec):
    label, atspi_name, cmd, kill_pattern = spec
    row = {"app": label, "atspi": atspi_name}
    started_here = False

    if desktop_app(atspi_name) is None:
        if cmd is None:
            row["verdict"] = "未运行且不自动启动"
            return row
        before = desktop_names()
        launch(cmd)
        started_here = True
        discovered = wait_for_new_app(before)
        if discovered is None:
            row["verdict"] = "启动后 AT-SPI 里查无此应用"
            row["_started"] = started_here
            return row
        if discovered != atspi_name:
            row["atspi"] = discovered + "  (预期 {})".format(atspi_name)
            atspi_name = discovered

    app = desktop_app(atspi_name)
    if app is None:
        row["verdict"] = "AT-SPI 节点消失"
        row["_started"] = started_here
        return row
    row["atspi_resolved"] = atspi_name
    row.update(raw_walk(app))
    snap = mcp_snapshot(atspi_name)
    row["mcp_ms"] = snap["ms"]
    row["mcp_lines"] = snap["lines"]
    row["mcp_chars"] = snap["chars"]
    row["mcp_error"] = snap.get("error")
    row["screenshot"] = snap.get("screenshot")
    row["_started"] = started_here
    return row


def verdict(r):
    if r.get("mcp_error"):
        return "MCP 失败"
    nodes = r.get("nodes", 0)
    act = r.get("actionable", 0)
    if nodes <= 2:
        return "无 a11y"
    if act == 0:
        return "有树但零可交互"
    if act < 5:
        return "树极弱"
    return "可用"


APPS = [
    # (显示名, AT-SPI 名, 启动命令, pkill 模式)
    ("LibreOffice Writer", "soffice", "soffice --writer --norestore", "soffice"),
    ("gedit", "gedit", "gedit /tmp/a11y-probe.txt", "gedit /tmp/a11y-probe"),
    ("Nautilus", "nautilus", "nautilus /tmp", "nautilus /tmp"),
    ("gnome-terminal", "gnome-terminal-server", None, None),  # 不碰，Claude Code 在里面
    ("Chrome (默认)", "Google Chrome", "google-chrome --no-first-run about:blank", "google-chrome"),
    ("Firefox", "firefox", "firefox --new-window about:blank", "firefox"),
    ("VS Code (默认)", "Code", "code --new-window /tmp", "/usr/share/code"),
    ("GIMP", "gimp", "gimp", "gimp"),
    ("VLC", "vlc", "vlc --intf qt", "vlc"),
    ("Thunderbird", "Thunderbird", "thunderbird", "thunderbird"),
]


def main():
    only = sys.argv[1:] or None
    rows = []
    for spec in APPS:
        if only and spec[0] not in only and spec[1] not in only:
            continue
        print("probing {} ...".format(spec[0]), flush=True)
        try:
            r = probe(spec)
        except Exception as e:
            r = {"app": spec[0], "atspi": spec[1], "verdict": "探测异常: {}".format(e)[:60]}
        r.setdefault("verdict", verdict(r))
        rows.append(r)
        if r.get("_started") and spec[3]:
            subprocess.run(["pkill", "-9", "-f", spec[3]], capture_output=True)
            time.sleep(2)
        print("   -> {}".format(r["verdict"]), flush=True)

    print("\n" + "=" * 118)
    hdr = "{:<20} {:>7} {:>7} {:>7} {:>7} {:>7} {:>8} {:>8} {:>9} {:>7}  {}"
    print(hdr.format("应用", "节点", "可见", "可交互", "可编辑", "深度",
                     "遍历ms", "MCP ms", "MCP字符", "≈token", "结论"))
    print("-" * 118)
    for r in rows:
        print(hdr.format(
            r["app"][:20],
            r.get("nodes", 0),
            r.get("visible", 0),
            r.get("actionable", 0),
            r.get("editable", 0),
            r.get("depth", 0),
            r.get("walk_ms", 0),
            r.get("mcp_ms", 0),
            r.get("mcp_chars", 0),
            r.get("mcp_chars", 0) // 4,
            r["verdict"] + (
                "  [截断]" if r.get("truncated") else ""
            ) + (
                "  [遍历超时]" if r.get("timeout") else ""
            ) + (
                "  [管理型容器x{}]".format(r["managed"]) if r.get("managed") else ""
            ),
        ))
    print("=" * 118)
    with open("/tmp/a11y-probe-result.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("原始结果: /tmp/a11y-probe-result.json")


if __name__ == "__main__":
    main()
