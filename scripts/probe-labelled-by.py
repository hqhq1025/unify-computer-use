#!/usr/bin/env python3
"""量测 AT-SPI 的 `LABELLED_BY` 关系值不值得读——**只量测，不改行为**。

为什么要单独做一次探针，而不是直接把它加进快照：

全仓 `grep get_relation_set|RelationType|LABELLED` 零命中，说明这条一等的
AT-SPI 信息源我们从来没读过。它专门用来把一个标签控件关联到它所描述的那个
**无名**控件上——听起来正好治我们最疼的那个病（手工跑 Impress 时，
「位置和大小」对话框里四个 spin button 全部无名，只能靠数顺序点，那是最脆的
一种定位）。

但"听起来对"不是上线的理由。这个仓库的规范是：没测过的路径不上线。所以先量
两个数，再决定做不做：

  命中率    有非空 LABELLED_BY 的节点占比
  净增益率  `name` 为空**且**有非空 LABELLED_BY 的节点占比

第二个数才是真正的收益。第一个数会被"本来就有名字的控件"灌水——一个已经叫
`Save` 的按钮再关联一个 `Save` 标签，对 agent 没有任何新信息。

还要量成本：`get_relation_set` 是每节点一次 DBus 往返。GIMP 现在
`get_app_state` 已经 30s 超时，不能再加负担。

用法：
  scripts/probe-labelled-by.py                    # 量所有正在运行的应用
  scripts/probe-labelled-by.py soffice gedit      # 只量指定的
"""

import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

MAX_NODES = 4000
MAX_DEPTH = 64


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def node_name(node):
    return safe(lambda: node.get_name(), "") or ""


def node_role(node):
    return safe(lambda: node.get_role_name(), "") or ""


def labelled_by(node):
    """返回这个节点的 LABELLED_BY 目标的名字，拼成一串。

    一个控件可以被多个标签描述（实测里少见，但规范允许），所以取全部再拼。
    """
    relations = safe(lambda: node.get_relation_set(), None)
    if not relations:
        return ""
    names = []
    for relation in relations:
        if safe(lambda: relation.get_relation_type()) != Atspi.RelationType.LABELLED_BY:
            continue
        count = safe(lambda: relation.get_n_targets(), 0) or 0
        for index in range(count):
            target = safe(lambda: relation.get_target(index))
            if target is None:
                continue
            text = node_name(target).strip()
            if text:
                names.append(text)
    return " ".join(names)


def walk(root, budget):
    """深度优先遍历，带节点预算。预算耗尽就停——探针不该把桌面拖死。"""
    stack = [(root, 0)]
    while stack and budget[0] > 0:
        node, depth = stack.pop()
        budget[0] -= 1
        yield node, depth
        if depth >= MAX_DEPTH:
            continue
        count = safe(lambda: node.get_child_count(), 0) or 0
        for index in reversed(range(min(count, 200))):
            child = safe(lambda: node.get_child_at_index(index))
            if child is not None:
                stack.append((child, depth + 1))


def probe_app(app):
    name = node_name(app)
    budget = [MAX_NODES]
    total = 0
    with_label = 0
    unnamed = 0
    unnamed_with_label = 0
    samples = []
    relation_seconds = 0.0

    for node, _ in walk(app, budget):
        total += 1
        own = node_name(node).strip()
        started = time.monotonic()
        label = labelled_by(node)
        relation_seconds += time.monotonic() - started
        if label:
            with_label += 1
        if not own:
            unnamed += 1
            if label:
                unnamed_with_label += 1
                if len(samples) < 8:
                    samples.append((node_role(node), label))

    return {
        "app": name,
        "total": total,
        "with_label": with_label,
        "unnamed": unnamed,
        "unnamed_with_label": unnamed_with_label,
        "relation_seconds": relation_seconds,
        "samples": samples,
    }


def percent(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def main():
    wanted = [a.lower() for a in sys.argv[1:]]
    desktop = Atspi.get_desktop(0)
    results = []
    for index in range(safe(lambda: desktop.get_child_count(), 0) or 0):
        app = safe(lambda: desktop.get_child_at_index(index))
        if app is None:
            continue
        name = node_name(app)
        if not name:
            continue
        if wanted and not any(w in name.lower() for w in wanted):
            continue
        started = time.monotonic()
        try:
            result = probe_app(app)
        except Exception as error:
            print("{:<24} 探测失败: {}".format(name, error))
            continue
        result["wall_seconds"] = time.monotonic() - started
        results.append(result)

    if not results:
        print("没有可探测的应用")
        return 1

    print("{:<22} {:>6} {:>10} {:>8} {:>12} {:>10}".format(
        "应用", "节点", "有标签", "无名", "无名+有标签", "关系耗时"))
    print("-" * 74)
    totals = {"total": 0, "with_label": 0, "unnamed": 0, "unnamed_with_label": 0,
              "relation_seconds": 0.0}
    for result in results:
        for key in totals:
            totals[key] += result[key]
        print("{:<22} {:>6} {:>9.1f}% {:>8} {:>11.1f}% {:>9.2f}s".format(
            result["app"][:22],
            result["total"],
            percent(result["with_label"], result["total"]),
            result["unnamed"],
            percent(result["unnamed_with_label"], result["unnamed"]),
            result["relation_seconds"],
        ))
    print("-" * 74)
    print("合计 {} 节点".format(totals["total"]))
    print("  命中率（有 LABELLED_BY）        {:.1f}%".format(
        percent(totals["with_label"], totals["total"])))
    print("  **净增益率**（无名且有标签／全部节点） {:.2f}%".format(
        percent(totals["unnamed_with_label"], totals["total"])))
    print("  无名节点中能补上名字的比例        {:.1f}%".format(
        percent(totals["unnamed_with_label"], totals["unnamed"])))
    print("  get_relation_set 总耗时          {:.2f}s（{:.3f}ms/节点）".format(
        totals["relation_seconds"],
        1000 * totals["relation_seconds"] / max(totals["total"], 1)))

    print()
    print("能补上名字的样本（role -> 补到的名字）：")
    shown = 0
    for result in results:
        for role, label in result["samples"]:
            print("  [{}] {:<26} -> {}".format(result["app"][:12], role, label))
            shown += 1
            if shown >= 20:
                break
        if shown >= 20:
            break
    if shown == 0:
        print("  （一个都没有）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
