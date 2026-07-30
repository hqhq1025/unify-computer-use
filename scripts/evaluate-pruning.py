#!/usr/bin/env python3
"""离线评测裁剪方案：保留率与压缩率（待办 #7）。

裁剪的核心风险是"砍掉了任务真正需要的元素而不自知"。这个脚本回答两个数：

- **保留率**：轨迹里被实际操作过的元素，裁剪后有多少仍然存在且可定位
- **压缩率**：裁剪后的 token 相对原始的比例

理想是保留率 100%、压缩率尽量低。任何让保留率掉到 100% 以下的方案都要单独
审视丢了什么——省下的 token 换不回一个做不成的任务。

评测是**离线**的：吃 `scripts/record-trajectory.py` 录下来的轨迹，不需要桌面会话，
因此可以反复跑、可以进 CI，改一版裁剪就重算一次。

用法:
  scripts/evaluate-pruning.py --trajectory /tmp/traj.jsonl
  scripts/evaluate-pruning.py --trajectory a.jsonl --trajectory b.jsonl --strategy visible-only
  scripts/evaluate-pruning.py --list-strategies
"""

import argparse
import json
import os
import sys

# OSWorld 官方 `judge_node()` 的角色白名单（mm_agents/accessibility_tree_wrap/
# heuristic_retrieve.py）。照抄过来是为了让"与官方持平"成为可度量的下限，
# 而不是一句口号。
OSWORLD_ROLES = {
    "document", "item", "button", "heading", "label", "scrollbar", "searchbox",
    "textbox", "link", "tabelement", "textfield", "textarea", "menu",
    "alert", "canvas", "check-box", "combo-box", "entry", "icon", "image",
    "paragraph", "scroll-bar", "section", "slider", "static", "table-cell",
    "terminal", "text",
}

# 结构性容器：无可操作价值，是最先该丢的一类。
STRUCTURAL_ROLES = {"filler", "panel", "separator", "scroll pane", "viewport", "split pane"}


def parse_line(line):
    """从渲染行里拆出 (index, role, name, 有无 Frame)。"""
    stripped = line.strip()
    if not stripped or not stripped[0].isdigit():
        return None
    index, _, rest = stripped.partition(" ")
    has_frame = " Frame: {" in rest
    body = rest
    for marker in (" More actions:", " Frame: {", " Value: ", " ["):
        position = body.find(marker)
        if position >= 0:
            body = body[:position]
    body = body.strip()
    # role 可能多词（check menu item / push button），name 是剩下的部分。
    # 用已知角色词逐步吃掉前缀，吃不动就整体当 role。
    role, name = body, ""
    for width in (3, 2, 1):
        parts = body.split()
        if len(parts) > width:
            candidate = " ".join(parts[:width])
            if candidate in KNOWN_ROLES:
                role, name = candidate, " ".join(parts[width:])
                break
    else:
        parts = body.split(None, 1)
        if parts:
            role = parts[0]
            name = parts[1] if len(parts) > 1 else ""
    try:
        return int(index), role, name, has_frame
    except ValueError:
        return None


KNOWN_ROLES = {
    "check menu item", "radio menu item", "menu item", "push button", "toggle button",
    "radio button", "check box", "combo box", "page tab list", "page tab",
    "table cell", "table column header", "scroll pane", "split pane", "spin button",
    "list box", "tool bar", "status bar", "menu bar", "popup menu", "text", "menu",
    "label", "panel", "filler", "separator", "icon", "image", "frame", "dialog",
    "window", "document", "paragraph", "link", "entry", "slider", "table", "cell",
    "viewport", "scroll bar", "tree", "tree item", "list", "list item", "terminal",
}


# --- 裁剪策略：输入渲染行列表，输出保留下来的行列表 ---

def strategy_none(lines):
    return list(lines)


def strategy_visible_only(lines):
    """H1：只保留屏幕上可见的节点（渲染行里带 Frame 即为可见）。

    `extents()` 已经过滤掉未渲染控件的 INT_MIN 哨兵坐标，所以"有 Frame"
    等价于"在屏幕上"。
    """
    out = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None or parsed[3]:
            out.append(line)
    return out


def strategy_drop_structural(lines):
    """H3：丢掉无名的纯结构性容器。有名字的容器仍然保留——它可能是可点的分组。"""
    out = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            out.append(line)
            continue
        _, role, name, _ = parsed
        if role in STRUCTURAL_ROLES and not name:
            continue
        out.append(line)
    return out


def strategy_osworld_roles(lines):
    """忠实对齐 OSWorld 官方 `judge_node()`。

    官方用的是**前缀/后缀**匹配而不是精确集合：
      startswith("document") 或 endswith("item"/"button"/"heading"/"label"/
      "scrollbar"/"searchbox"/"textbox"/"link"/"tabelement"/"textfield"/
      "textarea"/"menu")，再并上一个精确角色集；
    并且要求 Ubuntu 侧 showing 且 visible。
    照抄它的匹配语义很重要——用更严的规则去比，等于给自己放水。
    """
    suffixes = ("item", "button", "heading", "label", "scrollbar", "searchbox",
                "textbox", "link", "tabelement", "textfield", "textarea", "menu")
    out = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            out.append(line)
            continue
        _, role, _, has_frame = parsed
        if not has_frame:          # 对应官方的 showing && visible
            continue
        # 官方的 tag 是 XML 化的角色名，空格会被规整掉；两种写法都试
        for tag in (role.replace(" ", ""), role.replace(" ", "-"), role):
            if tag.startswith("document") or tag.endswith(suffixes) or tag in OSWORLD_ROLES:
                out.append(line)
                break
    return out


def strategy_flat(lines):
    """H2：扁平索引列表，去掉缩进层级。"""
    return [line.strip() for line in lines]


def strategy_visible_and_flat(lines):
    return strategy_flat(strategy_visible_only(lines))


STRATEGIES = {
    "none": (strategy_none, "基线，不裁剪"),
    "visible-only": (strategy_visible_only, "H1：只保留屏幕可见节点"),
    "drop-structural": (strategy_drop_structural, "H3：丢掉无名的纯结构容器"),
    "osworld-roles": (strategy_osworld_roles, "对齐 OSWorld 官方 judge_node（角色白名单 + 可见）"),
    "flat": (strategy_flat, "H2：扁平列表，去掉缩进"),
    "visible+flat": (strategy_visible_and_flat, "H1 + H2 组合"),
}


def approx_tokens(lines):
    return sum(len(line) for line in lines) // 4


def target_survives(target, kept_lines):
    """目标元素在裁剪后是否仍然存在且可定位。

    用录制时的完整渲染行做匹配，但**忽略 index**——裁剪会让编号重排，
    而 agent 关心的是"这个元素还在不在、还能不能按 role+name 找到它"。
    """
    identity = (target or {}).get("identity", "")
    if not identity:
        return None
    parsed = parse_line(identity)
    if parsed is None:
        return None
    _, role, name, _ = parsed
    for line in kept_lines:
        other = parse_line(line)
        if other is None:
            continue
        if other[1] == role and other[2] == name:
            return True
    return False


def evaluate(paths, names):
    steps = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    steps.append(json.loads(line))
    if not steps:
        print("轨迹里没有任何步骤", file=sys.stderr)
        return 1

    targeted = [s for s in steps if (s.get("target") or {}).get("identity")]
    print("轨迹：{} 步，其中 {} 步有元素定向目标\n".format(len(steps), len(targeted)))

    header = "{:<16} {:>10} {:>10} {:>10} {:>12}"
    print(header.format("策略", "原始token", "裁剪后", "压缩率", "保留率"))
    print("-" * 62)

    baseline_tokens = sum(approx_tokens(s["tree"]["lines"]) for s in steps)
    rows = []
    for name in names:
        fn, _ = STRATEGIES[name]
        kept_tokens = 0
        survived = 0
        checked = 0
        losses = []
        for step in steps:
            kept = fn(step["tree"]["lines"])
            kept_tokens += approx_tokens(kept)
            target = step.get("target")
            if target and target.get("identity"):
                checked += 1
                ok = target_survives(target, kept)
                if ok:
                    survived += 1
                elif ok is False:
                    losses.append((step["step"], target.get("描述", "")))
        ratio = 100 * kept_tokens // max(baseline_tokens, 1)
        retention = "n/a" if not checked else "{}/{} ({}%)".format(
            survived, checked, 100 * survived // checked)
        print(header.format(name, baseline_tokens, kept_tokens, "{}%".format(ratio), retention))
        rows.append((name, ratio, survived, checked, losses))

    print()
    for name, ratio, survived, checked, losses in rows:
        if losses:
            print("{} 丢失了这些被实际操作过的元素：".format(name))
            for step, desc in losses[:6]:
                print("   step {}: {}".format(step, desc[:56]))
    if not any(r[4] for r in rows):
        print("所有策略的保留率均为 100%——没有任何被操作过的元素被裁掉。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="离线评测裁剪方案的保留率与压缩率")
    parser.add_argument("--trajectory", action="append", default=[],
                        help="轨迹文件，可重复指定")
    parser.add_argument("--strategy", action="append", default=[],
                        help="要评测的策略，可重复；默认全部")
    parser.add_argument("--list-strategies", action="store_true")
    args = parser.parse_args()

    if args.list_strategies:
        print("可用策略：")
        for name, (_, desc) in STRATEGIES.items():
            print("  {:16} {}".format(name, desc))
        return 0

    if not args.trajectory:
        print("需要 --trajectory；先用 scripts/record-trajectory.py 录一条",
              file=sys.stderr)
        return 2
    for path in args.trajectory:
        if not os.path.exists(path):
            print("找不到轨迹文件 {}".format(path), file=sys.stderr)
            return 2

    names = args.strategy or list(STRATEGIES)
    for name in names:
        if name not in STRATEGIES:
            print("未知策略 {!r}，用 --list-strategies 查看".format(name), file=sys.stderr)
            return 2
    return evaluate(args.trajectory, names)


if __name__ == "__main__":
    sys.exit(main())
