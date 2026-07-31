#!/usr/bin/env python3
"""验证 GUI 通道能不能**自给自足**：agent 只靠截图定位，不靠无障碍树。

为什么要单独验这一条：两次真实 agent 跑 OSWorld 时，坐标**都来自树的 Frame**，
不是从图上读的。所以"GUI 通道能用"这件事一直没被证明——只证明了"坐标能从树里
算出来"。这是 P3（几何改成 opt-in）的硬前置：树不给几何之后，坐标只能来自截图。

判据是**我自己截的图**，不采信 agent 的任何说法：
  1. 动作前截一张，从中找出目标色块在屏幕上的位置（纯红像素）
  2. 动作后再截一张，找出所有变化的像素
  3. 变化像素的重心落在目标块内 → 通过

目标块在 GIMP 画布上，而 GIMP 画布在 AT-SPI 树里**一个节点都没有**，
所以这条链路除了看图别无他法。
"""

import os
import sys

from PIL import Image

RED = (220, 40, 40)
TOLERANCE = 40


def shot(path):
    """用 Gdk 抓全屏——**不要用 ImageMagick 的 import**。

    实测 `import` 会抓取 X server，把打开的菜单弹掉；本仓库的截图走
    `Gdk.pixbuf_get_from_window`，不会。判据工具自己去改变被测状态，
    那就不是判据了。
    """
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    screen = Gdk.Screen.get_default()
    root = screen.get_root_window()
    pixbuf = Gdk.pixbuf_get_from_window(
        root, 0, 0, screen.get_width(), screen.get_height())
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    with open(path, "wb") as handle:
        handle.write(bytes(data))
    return Image.open(path).convert("RGB")


def find_colour(image, target, tolerance=TOLERANCE):
    """返回目标色块的包围盒——取**最密的那一簇**，不是全局包围盒。

    两个坑都踩过：
    1. 只判"接近目标色"会把 GIMP 深色主题里一大片偏暖像素也算进来，
       第一版的包围盒直接是整个屏幕。所以还要求红分量显著压过绿蓝。
    2. 即便如此，界面装饰上仍有零散的强红像素（实测 1928 个里有约 290 个
       散落在四角）。全局包围盒会被这些噪点撑满，所以要先按网格找出主簇，
       再只在主簇附近取边界。
    """
    pixels = image.load()
    width, height = image.size
    points = []
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r, g, b = pixels[x, y]
            if not (abs(r - target[0]) <= tolerance and abs(g - target[1]) <= tolerance
                    and abs(b - target[2]) <= tolerance):
                continue
            # 主通道要显著压过另外两个。判据写死"红占优"的话，蓝绿两个对照块
            # 就一个都找不到——而没有对照块，"命中目标"就证明不了"没误伤旁边"。
            channels = [r, g, b]
            dominant = list(target).index(max(target))
            others = [c for i, c in enumerate(channels) if i != dominant]
            if channels[dominant] - max(others) >= 60:
                points.append((x, y))
    if not points:
        return None
    cells = {}
    for x, y in points:
        cells.setdefault((x // 100, y // 100), []).append((x, y))
    cx, cy = max(cells, key=lambda k: len(cells[k]))
    seed_x = cx * 100 + 50
    seed_y = cy * 100 + 50
    near = [(x, y) for x, y in points
            if abs(x - seed_x) <= 200 and abs(y - seed_y) <= 200]
    xs = [p[0] for p in near]
    ys = [p[1] for p in near]
    return (min(xs), min(ys), max(xs), max(ys))


BLUE = (40, 90, 220)
GREEN = (40, 180, 70)


def region_change(before, after, box):
    """box 里有多少比例的采样点变了。"""
    a, b = before.load(), after.load()
    total = changed = 0
    for y in range(box[1], box[3] + 1, 2):
        for x in range(box[0], box[2] + 1, 2):
            total += 1
            p, q = a[x, y], b[x, y]
            if abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) > 60:
                changed += 1
    return changed, total


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "before"
    if mode == "before":
        image = shot("/tmp/vlm-before.png")
        box = find_colour(image, RED)
        if box is None:
            print("找不到红色目标块")
            return 1
        print("红块屏幕包围盒 {}，中心 ({}, {})".format(
            box, (box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
        return 0

    before = Image.open("/tmp/vlm-before.png").convert("RGB")
    after = (Image.open("/tmp/vlm-after.png").convert("RGB")
             if os.environ.get("OCU_REUSE_AFTER") else shot("/tmp/vlm-after.png"))

    # 判据只看**三个色块各自变了多少**，不看全屏。
    # 第一版算的是全屏变化像素的重心，结果被工具选项面板、菜单开合这些 UI 变化
    # 带偏（14998 个变化像素，重心落在画布外）——判据把噪声当成了信号。
    targets = {
        "红块(目标)": find_colour(before, RED),
        "蓝块": find_colour(before, BLUE, tolerance=60),
        "绿块": find_colour(before, GREEN, tolerance=60),
    }
    ratios = {}
    for name, box in targets.items():
        if box is None:
            print("{} 找不到".format(name))
            continue
        changed, total = region_change(before, after, box)
        ratios[name] = 100 * changed // max(total, 1)
        print("{:<12} {:<26} {:>5}/{:<5} = {}%".format(
            name, str(box), changed, total, ratios[name]))

    hit = ratios.get("红块(目标)", 0) >= 5
    clean = all(ratios.get(k, 100) <= 2 for k in ("蓝块", "绿块"))
    print()
    if hit and clean:
        print("✅ GUI 通道自给自足：只靠截图定位，命中了 a11y 树里零存在的目标，"
              "且没有误伤旁边的色块")
        return 0
    print("❌ 目标变化 {}%，旁边色块变化 {}".format(
        ratios.get("红块(目标)"), {k: ratios.get(k) for k in ("蓝块", "绿块")}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
