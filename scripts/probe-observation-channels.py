#!/usr/bin/env python3
"""a11y 盲区里还有什么**别的**可观测通道——只量测，不改行为。

跑 OSWorld 的过程中撞见的盲区，一次比一次贵：

  · GNOME 门户的二级确认框（"文件已存在，是否替换？"）——**整个窗口不在
    AT-SPI 树里**，却握着输入焦点，把第 4 题卡死
  · Chrome 书签气泡的文件夹下拉——选项一个都不在树里（第 7 题）
  · GIMP 画布——零节点
  · Chrome 打印对话框的下拉当前值——不在 Value 也不在 Text 接口上

前两个已经各自打了补丁，但补丁是**针对症状**的。这个探针问的是根上的问题：
**当 AT-SPI 什么都不给的时候，这台机器上还有哪些通道能看见东西？**

候选通道，按"能拿到多少结构信息"从高到低：

  1. AT-SPI                树、角色、名字、状态、几何 —— 最富，但会整块缺失
  2. X11 属性              窗口标题/类/PID/几何/类型/状态 —— 永远存在
  3. X11 窗口树            子窗口的位置与大小 —— 结构，但没有语义
  4. CDP（浏览器）          完整 DOM —— 只有浏览器有，且要开调试端口
  5. 像素                  什么都能看见，什么语义都没有

这个脚本对每个正在运行的窗口把 1、2、3 都量一遍，输出**每个通道各自看得见
多少**，用来回答"补哪个通道最划算"。**不猜，只量。**

用法：scripts/probe-observation-channels.py
"""

import json
import re
import subprocess
import sys

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def x11_windows():
    """X11 认识的所有可见顶层窗口。"""
    out = subprocess.run(["wmctrl", "-lpG"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        wid, _desk, pid, x, y, w, h, title = parts
        rows.append({"id": wid, "pid": int(pid), "title": title,
                     "geometry": (int(x), int(y), int(w), int(h))})
    return rows


def x11_children(window_id):
    """X11 层能看见的子窗口数量与几何。

    这是"结构但无语义"的那一档：GTK/Qt 的现代应用几乎把所有控件都画在一个
    X 窗口里，所以这个数字通常很小；但**对话框、菜单、popup 常常是独立的
    X 窗口**——而那恰恰是 AT-SPI 最容易整块缺失的地方。
    """
    out = subprocess.run(["xwininfo", "-id", window_id, "-children"],
                         capture_output=True, text=True).stdout
    kids = re.findall(r"^\s+(0x[0-9a-f]+)", out, re.M)
    return len(kids)


def x11_properties(window_id):
    """X11 属性里有多少条对定位/理解有用的信息。"""
    out = subprocess.run(["xprop", "-id", window_id], capture_output=True,
                         text=True).stdout
    useful = ("WM_CLASS", "WM_NAME", "_NET_WM_NAME", "_NET_WM_PID",
              "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE", "WM_TRANSIENT_FOR",
              "_NET_FRAME_EXTENTS", "_GTK_APPLICATION_ID")
    found = {}
    for name in useful:
        match = re.search(r"^{}\S*\s*=\s*(.+)$".format(name), out, re.M)
        if match:
            found[name] = match.group(1).strip()[:60]
    return found


def atspi_index():
    """AT-SPI 认识的窗口：标题 -> 节点数。"""
    desktop = safe(lambda: Atspi.get_desktop(0))
    index = {}
    if desktop is None:
        return index
    for i in range(safe(lambda: desktop.get_child_count(), 0) or 0):
        app = safe(lambda: desktop.get_child_at_index(i))
        if app is None:
            continue
        app_name = str(safe(lambda: app.get_name(), "") or "")
        for j in range(safe(lambda: app.get_child_count(), 0) or 0):
            window = safe(lambda: app.get_child_at_index(j))
            if window is None:
                continue
            title = str(safe(lambda: window.get_name(), "") or "")
            count = [0]

            def walk(node, depth):
                if count[0] > 3000 or depth > 30:
                    return
                count[0] += 1
                for k in range(min(safe(lambda: node.get_child_count(), 0) or 0, 120)):
                    child = safe(lambda: node.get_child_at_index(k))
                    if child is not None:
                        walk(child, depth + 1)

            walk(window, 0)
            index.setdefault(title, {"app": app_name, "nodes": 0})
            index[title]["nodes"] += count[0]
    return index


def main():
    tree = atspi_index()
    windows = x11_windows()
    print("{:<44} {:>7} {:>8} {:>7} {:>6}".format(
        "窗口标题", "a11y", "X11子窗", "X11属性", "PID"))
    print("-" * 78)
    blind = []
    for window in windows:
        title = window["title"]
        # wmctrl 的标题带主机名前缀，去掉
        clean = title.split(" ", 1)[-1] if title.startswith("user-") else title
        entry = tree.get(clean) or tree.get(title)
        nodes = entry["nodes"] if entry else 0
        kids = x11_children(window["id"])
        props = x11_properties(window["id"])
        print("{:<44} {:>7} {:>8} {:>7} {:>6}".format(
            clean[:44], nodes if entry else "**0**", kids, len(props), window["pid"]))
        if not entry or nodes <= 1:
            blind.append((clean, window, kids, props))

    print()
    if not blind:
        print("当前没有 a11y 盲区窗口。要抓到盲区，先把一个文件对话框/菜单打开再跑。")
    else:
        print("=== a11y 看不见或近乎看不见的窗口，X11 还能给什么 ===")
        for clean, window, kids, props in blind:
            print("\n  窗口 {!r}  pid={}  几何={}".format(
                clean[:50] or "(无标题)", window["pid"], window["geometry"]))
            print("    X11 子窗口 {} 个".format(kids))
            for key, value in sorted(props.items()):
                print("    {:<24} {}".format(key, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
