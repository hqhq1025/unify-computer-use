#!/usr/bin/env python3

import base64
import json
import math
import os
import sys
import time
import traceback
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi

gi.require_version("Atspi", "2.0")

try:
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
except (ImportError, ValueError):
    Gdk = None

from gi.repository import Atspi


MAX_ELEMENTS = 1200
MAX_DEPTH = 64
DEFAULT_TEXT_LIMIT = 500
# 抬窗时最多尝试几个 FOCUSABLE 控件。抓焦点会真的改变应用内的焦点位置，
# 所以只试少量最可能的候选，不要把整棵树扫一遍。
FOCUS_GRAB_CANDIDATES = 8
# 合理屏幕坐标/尺寸的上限。超出这个量级的只可能是未渲染控件的 INT_MIN 哨兵值，
# 再夸张的多显示器布局也到不了这个数量级。
MAX_SANE_EXTENT = 100000
# 预算用到这个比例之后，开始丢弃"无名 + 无动作 + 无值"的纯结构容器。
# 深度优先截断等于按遍历顺序随机丢弃，先到的占满配额、后面的整片消失；
# 而结构容器（filler / panel / separator）对 agent 没有可操作价值，
# 是唯一可以安全牺牲的一类。
BUDGET_PRESSURE_RATIO = 0.8


def frame(x, y, width, height):
    if width is None or height is None or width < 0 or height < 0:
        return None
    return {
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }


def safe(call, default=None):
    try:
        value = call()
        if value is None:
            return default
        return value
    except Exception:
        return default


def has_text_iface(node):
    """节点是否实现 Text 接口。

    不能用 Accessible.is_text —— 该便捷方法是 libatspi 2.52+ 才加入的，
    在 at-spi2-core 2.44（Ubuntu 22.04）上不存在。而且它是**属性访问**，
    会在传进 safe() 之前就抛 AttributeError，safe() 的 try/except 兜不住，
    导致 get_app_state 整个失败。

    get_text_iface() 在新旧版本都存在，是更基础也更可移植的判据。
    """
    return safe(node.get_text_iface) is not None


def has_editable_text_iface(node):
    """节点是否实现 EditableText 接口。理由同 has_text_iface。"""
    return safe(node.get_editable_text_iface) is not None


def require_desktop_session():
    missing = []
    if not os.environ.get("XDG_RUNTIME_DIR"):
        missing.append("XDG_RUNTIME_DIR")
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        missing.append("DBUS_SESSION_BUS_ADDRESS")
    if missing:
        raise RuntimeError(
            "Linux runtime requires an active desktop session; missing "
            + ", ".join(missing)
        )


def desktop():
    return Atspi.get_desktop(0)


def child_count(node):
    return int(safe(node.get_child_count, 0) or 0)


def child_at(node, index):
    return safe(lambda: node.get_child_at_index(index))


def node_name(node):
    return str(safe(node.get_name, "") or "")


def limit_text(value, text_limit=DEFAULT_TEXT_LIMIT):
    text = str(value or "")
    if text_limit is None:
        return text
    if len(text) > text_limit:
        return text[:text_limit] + "..."
    return text


def node_role(node):
    return str(safe(node.get_role_name, "") or "")


def node_pid(node):
    value = safe(node.get_process_id, 0)
    try:
        return int(value or 0)
    except Exception:
        return 0


def state_contains(node, state):
    state_set = safe(node.get_state_set)
    if state_set is None:
        return False
    return bool(safe(lambda: state_set.contains(state), False))


# 子节点数硬上限。LibreOffice Calc 的 sheet 节点会谎报 2^31 个子节点：
# 它的 accessible range 是整张表（16384 列 × 1048576 行），
# getAccessibleChildCount() 返回 rows*cols = 1.7e10，经 D-Bus int32 截断后
# 就是 2147483647。朴素遍历掉进这个节点不是"慢"，是永远不会结束。
# 源码：sc/source/ui/Accessibility/AccessibleTableBase.cxx:274
#       （上游注释原文：'FIXME: ... is a plain and simple madness'）
HARD_CHILD_CAP = int(os.environ.get("OPEN_COMPUTER_USE_MAX_CHILDREN", "4096"))

# 单个容器最多发起多少次 child_at()。与 HARD_CHILD_CAP 的区别：后者决定
# "要不要枚举"，这个决定"最多枚举几个"，用于兜住不声明 MANAGES_DESCENDANTS
# 却依然谎报子节点数的实现。
MAX_CHILD_FANOUT = int(os.environ.get("OPEN_COMPUTER_USE_MAX_FANOUT", "512"))

# find_first() 的节点预算。它被 focused_summary/selected_text 用于定位焦点，
# 原实现无上限，谓词不匹配时会尝试走遍整棵树。
FIND_FIRST_BUDGET = int(os.environ.get("OPEN_COMPUTER_USE_FIND_BUDGET", "4000"))

# 已被 click 工具覆盖的语义动作名。必须与 preferred_action_index() 里的
# preferred_exact 保持一致 —— 两者是同一件事的两面：一个负责调用，
# 一个负责不要重复展示。
CLICK_COVERED_ACTIONS = {
    "click",
    "press",
    "activate",
    "default.activate",
    "invoke",
    "select",
    "toggle",
    "open",
}

# 未展开的菜单不递归其子项。实测默认配额 1200 下，LibreOffice 的菜单树会占掉
# 100% 配额（一份完整菜单栏约 780 节点），表格单元格一个都进不来 —— 功能等于
# 不存在。只对菜单类角色应用该规则：其它中间层容器（panel / scroll pane）在
# LibreOffice 上普遍不设 SHOWING，一并过滤会把整棵树砍空。
MENU_ROLES = {
    "menu",
    "menu bar",
    "menu item",
    "check menu item",
    "radio menu item",
    "popup menu",
}


def should_enumerate_children(node):
    """是否应该枚举该节点的子节点。

    两道守卫都只读 libatspi 的本地缓存（ATSPI_CACHE_DEFAULT 覆盖 STATES 与
    CHILDREN），不产生 D-Bus 往返，因此可以无脑放在遍历热路径上：

    1. MANAGES_DESCENDANTS —— AT-SPI 规范对超大容器（表格等）的正式契约：
       "the children should not, and need not, be enumerated by the client"。
       Calc 的 sheet 正是这么标记的（AccessibleSpreadsheet.cxx:1066）。
       这类容器要用 Table.get_accessible_at(row, col) 按坐标寻址，
       或 Component.get_accessible_at_point() 做点命中，而不是枚举。
    2. child_count 硬上限 —— 兜住任何谎报子节点数的实现。
    """
    if state_contains(node, Atspi.StateType.MANAGES_DESCENDANTS):
        return False
    return child_count(node) <= HARD_CHILD_CAP


def extents(node):
    component = safe(node.get_component_iface)
    if component is None:
        return None
    rect = safe(lambda: Atspi.Component.get_extents(component, Atspi.CoordType.SCREEN))
    if (
        rect is None
        or rect.width <= 0
        or rect.height <= 0
        or rect.width > MAX_SANE_EXTENT
        or rect.height > MAX_SANE_EXTENT
    ):
        return None
    # 未渲染的控件在 GTK 上会返回 INT_MIN 量级的原点。尺寸看着正常（常见 1x1），
    # 所以只过滤 width/height 拦不住：它们会带着 -2147483648 这样的坐标进入
    # element 树，coordinate click/drag 打上去就落到无意义的位置。
    if abs(rect.x) > MAX_SANE_EXTENT or abs(rect.y) > MAX_SANE_EXTENT:
        return None
    return frame(rect.x, rect.y, rect.width, rect.height)


def relative_frame(node, window_bounds):
    bounds = extents(node)
    if bounds is None:
        return None
    if window_bounds is None:
        return bounds
    return frame(
        bounds["x"] - window_bounds["x"],
        bounds["y"] - window_bounds["y"],
        bounds["width"],
        bounds["height"],
    )


def iter_apps():
    root = desktop()
    apps = []
    for index in range(child_count(root)):
        app = child_at(root, index)
        if app is not None and node_name(app):
            apps.append(app)
    return apps


def app_windows(app):
    windows = []
    for index in range(child_count(app)):
        child = child_at(app, index)
        if child is None:
            continue
        role = node_role(child).lower()
        bounds = extents(child)
        if role in {"frame", "window", "dialog", "alert"} or bounds is not None:
            windows.append((index, child))
    return windows


def main_window(app):
    """挑出当前真正该操作的顶层窗口。

    顺序：可见的模态对话框 > ACTIVE > SHOWING > 第一个。

    模态对话框必须排在 ACTIVE 前面。macOS 侧可以直接问
    `kAXFocusedWindowAttribute`，AT-SPI 没有等价属性，只能从状态推断；而
    LibreOffice 这类应用的 frame 和 dialog **都不上报 ACTIVE**，于是判据会一路
    落到 SHOWING，模态对话框就因为在子节点顺序里排得靠后而输给主窗口——
    结果是只要弹出对话框，agent 拿到的就是主窗口的树，**完全看不见对话框**。
    而对话框恰恰是 OSWorld 里最主要的操作对象。

    模态状态本身就是最强判据：MODAL 按定义阻塞了应用其余部分的交互，
    它就是此刻唯一可操作的窗口。

    但模态窗口可能同时存在多个：combo box 的下拉在 LibreOffice 里是一个独立的
    顶层 window，状态为 MODAL + SHOWING + **ACTIVE**，而它下面的对话框只有
    MODAL + SHOWING。只按"第一个模态"取会拿到对话框，下拉里的选项仍然看不见。
    所以模态候选里再按 ACTIVE 细分一次，取最上层的那个。
    """
    windows = app_windows(app)
    if not windows:
        raise RuntimeError(
            "No top-level AT-SPI window is available for " + node_name(app)
        )
    modal = [
        (index, window)
        for index, window in windows
        if state_contains(window, Atspi.StateType.MODAL)
        and state_contains(window, Atspi.StateType.SHOWING)
    ]
    for index, window in modal:
        if state_contains(window, Atspi.StateType.ACTIVE):
            return index, window
    if modal:
        return modal[0]
    for index, window in windows:
        if state_contains(window, Atspi.StateType.ACTIVE):
            return index, window
    for index, window in windows:
        if state_contains(window, Atspi.StateType.SHOWING):
            return index, window
    return windows[0]


def matches_query(app, query):
    normalized = query.strip().lower()
    if not normalized:
        return False
    if normalized.isdigit() and node_pid(app) == int(normalized):
        return True
    app_name = node_name(app).lower()
    if app_name == normalized or normalized in app_name:
        return True
    for _, window in app_windows(app):
        title = node_name(window).lower()
        if title == normalized or normalized in title:
            return True
    return False


def resolve_app(query):
    for app in iter_apps():
        if matches_query(app, query):
            return app
    raise RuntimeError('appNotFound("{}")'.format(query))


def action_names(node):
    """列出该节点**尚未被 click 工具覆盖**的语义动作。

    必须过滤掉 preferred_action_index() 会挑中的那些（click/press/activate/…），
    否则每个可点击节点都会显示 "Secondary Actions: click"，而那恰恰就是
    click 工具自己要调用的动作。重复列出会让模型误以为那是另一条备选路径，
    进而在 click(element_index) 与 perform_secondary_action 之间反复摇摆，
    甚至退回坐标点击。

    macOS 侧的 meaningfulActions() 出于同样理由过滤掉
    AXPress / AXConfirm / AXOpen / AXShowMenu。这里与之对齐。
    """
    names = []
    count = int(safe(node.get_n_actions, 0) or 0)
    for index in range(count):
        name = str(safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(
            safe(lambda i=index: node.get_action_description(i), "") or ""
        )
        label = name or description
        if not label or label in names:
            continue
        if label.strip().lower() in CLICK_COVERED_ACTIONS:
            continue
        names.append(label)
    return names


def accessible_id(node):
    return str(safe(node.get_accessible_id, "") or "")


def text_value(node, text_limit=DEFAULT_TEXT_LIMIT):
    if not has_text_iface(node):
        return ""
    text_iface = safe(node.get_text_iface)
    if text_iface is None:
        return ""
    count = int(safe(lambda: Atspi.Text.get_character_count(text_iface), 0) or 0)
    if count <= 0:
        return ""
    end_offset = count if text_limit is None else min(count, text_limit + 1)
    value = str(safe(lambda: Atspi.Text.get_text(text_iface, 0, end_offset), "") or "")
    return limit_text(value, text_limit=text_limit)


def numeric_value(node):
    value_iface = safe(node.get_value_iface)
    if value_iface is None:
        return ""
    current = safe(lambda: Atspi.Value.get_current_value(value_iface))
    if current is None:
        return ""
    return str(current)


def element_value(node, text_limit=DEFAULT_TEXT_LIMIT):
    return text_value(node, text_limit=text_limit) or numeric_value(node)


def positive_int(value, fallback):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, float) and not value.is_integer():
        return fallback
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return fallback
    return integer if integer > 0 else fallback


def parse_text_limit(value, fallback=DEFAULT_TEXT_LIMIT):
    if isinstance(value, str) and value.lower() == "max":
        return None
    return positive_int(value, fallback)


# 对决策有用、但此前完全不在树里体现的状态。只渲染"非默认"的那一侧：
# 每个节点都标 enabled/not-focused 只会淹没信号，而 disabled / checked /
# expanded / selected / focused 才是 agent 需要据以决策的信息。
NOTABLE_STATES = (
    ("CHECKED", "checked"),
    ("EXPANDED", "expanded"),
    ("SELECTED", "selected"),
    ("FOCUSED", "focused"),
)


def state_segment(node):
    """把值得关注的状态渲染成紧凑标记，如 `[disabled checked]`。

    禁用单独处理：它是"缺少 ENABLED"而不是"具备某状态"，而 agent 对着一个
    禁用控件反复点击是很常见的浪费。
    """
    marks = []
    if not state_contains(node, Atspi.StateType.ENABLED):
        marks.append("disabled")
    for name, label in NOTABLE_STATES:
        state = getattr(Atspi.StateType, name, None)
        if state is not None and state_contains(node, state):
            marks.append(label)
    if not marks:
        return ""
    return " [" + " ".join(marks) + "]"


def record_for(node, index, path, window_bounds, text_limit=DEFAULT_TEXT_LIMIT):
    bounds = relative_frame(node, window_bounds)
    role = node_role(node)
    return {
        "index": index,
        "runtimeId": path[:],
        "automationId": accessible_id(node),
        "name": limit_text(node_name(node), text_limit=text_limit),
        "controlType": role,
        "localizedControlType": role,
        "className": str(safe(node.get_toolkit_name, "") or ""),
        "value": element_value(node, text_limit=text_limit),
        "nativeWindowHandle": 0,
        "frame": bounds,
        "actions": action_names(node),
        "states": state_segment(node),
    }


# 自管理表格容器一次最多渲染多少单元格。典型视口约 37 行 × 21 列 = 777 个，
# 实测取回（含文本）0.12s，0.2ms/cell。设上限兜住超宽表格。
MAX_TABLE_CELLS = int(os.environ.get("OPEN_COMPUTER_USE_MAX_TABLE_CELLS", "1200"))


def visible_cell_range(node):
    """探测自管理表格容器当前视口对应的 (row0, row1, col0, col1)。

    MANAGES_DESCENDANTS 容器不能枚举子节点（Calc 的 sheet 谎报 10.7 亿个），
    但可以按坐标寻址。做法是用 Component.get_accessible_at_point 打容器矩形的
    两个对角，反解出可见的行列范围——在 Calc 里这是 O(1) 的像素→行列换算
    （ScAccessibleSpreadsheet::getAccessibleAtPoint 走 GetPosFromPixel）。

    返回 None 表示探测失败，调用方应回退到"不枚举"。
    """
    table = safe(node.get_table_iface)
    component = safe(node.get_component_iface)
    if table is None or component is None:
        return None
    rect = safe(
        lambda: Atspi.Component.get_extents(component, Atspi.CoordType.SCREEN)
    )
    if rect is None or rect.width <= 0 or rect.height <= 0:
        return None

    corners = [
        (rect.x + 3, rect.y + 3),
        (rect.x + rect.width - 4, rect.y + rect.height - 4),
    ]
    found = []
    for x, y in corners:
        cell = safe(
            lambda x=x, y=y: Atspi.Component.get_accessible_at_point(
                component, x, y, Atspi.CoordType.SCREEN
            )
        )
        if cell is None:
            return None
        index = safe(cell.get_index_in_parent)
        if index is None:
            return None
        row = safe(lambda: Atspi.Table.get_row_at_index(table, index))
        col = safe(lambda: Atspi.Table.get_column_at_index(table, index))
        if row is None or col is None:
            return None
        found.append((int(row), int(col)))

    if len(found) != 2:
        return None
    rows = sorted(f[0] for f in found)
    cols = sorted(f[1] for f in found)
    return rows[0], rows[1], cols[0], cols[1]


def render_visible_cells(
    node, depth, path, window_bounds, records, lines,
    text_limit=DEFAULT_TEXT_LIMIT, max_tree_nodes=MAX_ELEMENTS,
):
    """把自管理表格容器当前视口内的单元格渲染进树，返回渲染数量。

    这是 should_enumerate_children() 拒绝枚举之后的替代路径：枚举会掉进
    Calc 谎报的 10.7 亿子节点，而按 (row, col) 寻址在 Calc 里是 O(1) 查表。
    """
    span = visible_cell_range(node)
    if span is None:
        return 0
    row0, row1, col0, col1 = span
    table = safe(node.get_table_iface)
    if table is None:
        return 0

    total = (row1 - row0 + 1) * (col1 - col0 + 1)
    budget = min(MAX_TABLE_CELLS, max(0, max_tree_nodes - len(records)))
    count = 0
    for row in range(row0, row1 + 1):
        for col in range(col0, col1 + 1):
            if count >= budget:
                break
            cell = safe(lambda r=row, c=col: Atspi.Table.get_accessible_at(table, r, c))
            if cell is None:
                continue
            index = len(records)
            record = record_for(
                cell, index, path + [index], window_bounds, text_limit=text_limit
            )
            records.append(record)
            # 只取文本，不回退到 numeric_value：Calc 的空单元格 Value 接口
            # 返回 0.0，会让空白单元格看起来像是填了 0 —— 对"找出空单元格"
            # 这类任务是致命的误导。
            value = text_value(cell, text_limit=text_limit)
            lines.append(
                ("\t" * (depth + 2))
                + "{} cell R{}C{} {}".format(index, row, col, value).rstrip()
            )
            count += 1
        if count >= budget:
            break

    if count:
        lines.append(
            ("\t" * (depth + 2))
            + "(showing {} of {} cells in view; table is {}x{} — "
              "address other cells by row/column)".format(
                  count, total,
                  safe(lambda: Atspi.Table.get_n_rows(table), "?"),
                  safe(lambda: Atspi.Table.get_n_columns(table), "?"),
              )
        )
    return count


def render_tree(root, window_bounds, root_path, text_limit=DEFAULT_TEXT_LIMIT, max_tree_nodes=MAX_ELEMENTS, max_tree_depth=MAX_DEPTH):
    records = []
    lines = []

    dropped = {"count": 0}
    pressure_at = max(1, int(max_tree_nodes * BUDGET_PRESSURE_RATIO))

    def is_structural_filler(record):
        """无名、无动作、无值的纯容器。对 agent 没有可操作价值。"""
        return not (record["name"] or record["actions"] or record["value"])

    def visit(node, depth, path):
        if len(records) >= max_tree_nodes or depth > max_tree_depth or node is None:
            if node is not None and len(records) >= max_tree_nodes:
                dropped["count"] += 1
            return
        index = len(records)
        record = record_for(node, index, path, window_bounds, text_limit=text_limit)

        # 预算吃紧时优先保住有名字/有动作/有值的节点。丢容器只丢它自己这一行，
        # 仍然继续递归子节点——被丢的容器往往正是有价值控件的父节点。
        if len(records) >= pressure_at and is_structural_filler(record) and depth > 0:
            dropped["count"] += 1
            for child_index in range(min(child_count(node), MAX_CHILD_FANOUT)):
                visit(child_at(node, child_index), depth + 1, path + [child_index])
            return

        records.append(record)

        role = record["localizedControlType"] or record["controlType"] or "element"
        title = record["name"] or record["automationId"] or ""
        value_segment = ""
        if record["value"] and record["value"] != title:
            safe_value = record["value"].replace("\r", "\\r").replace("\n", "\\n")
            value_segment = " Value: " + safe_value
        state_seg = record.get("states", "")
        actions_segment = ""
        if record["actions"]:
            actions_segment = " More actions: " + ", ".join(record["actions"])
        frame_segment = ""
        if record["frame"] is not None:
            f = record["frame"]
            frame_segment = " Frame: {{x: {0}, y: {1}, width: {2}, height: {3}}}".format(
                round(f["x"]),
                round(f["y"]),
                round(f["width"]),
                round(f["height"]),
            )
        lines.append(
            ("\t" * (depth + 1))
            + "{} {} {}{}{}{}{}".format(
                index, role, title, state_seg, value_segment, actions_segment, frame_segment
            ).rstrip()
        )

        # 未展开的菜单：保留节点自身（它是 perform_secondary_action 的入口），
        # 但不递归其子项。role 与 state 都走 libatspi 本地缓存，零 D-Bus 成本。
        if (
            depth > 0
            and role.lower() in MENU_ROLES
            and not state_contains(node, Atspi.StateType.SHOWING)
        ):
            pending = child_count(node)
            if pending:
                lines.append(
                    ("\t" * (depth + 2))
                    + "({} items collapsed; activate this menu to expand)".format(pending)
                )
            return

        if not should_enumerate_children(node):
            # 超大/自管理容器不能枚举，改用坐标寻址取当前视口内的单元格。
            rendered = render_visible_cells(
                node, depth, path, window_bounds, records, lines,
                text_limit=text_limit, max_tree_nodes=max_tree_nodes,
            )
            if rendered == 0:
                # 寻址也失败时显式说明，避免模型以为这里本来就是空的。
                lines.append(
                    ("\t" * (depth + 2))
                    + "(contents not enumerated: {} manages its own descendants "
                      "and cell addressing failed)".format(role)
                )
            return

        # 配额检查必须在 child_at() **之前**。visit() 开头虽然也查配额并立刻
        # return，但 range(child_count) 这个循环本身不会停 —— 面对谎报十亿
        # 子节点的容器，光是发起那些注定被丢弃的 child_at() 往返就要数十小时。
        # 这是比 should_enumerate_children() 更底层的兜底：后者依赖容器正确
        # 声明 MANAGES_DESCENDANTS，而这里不依赖任何声明。
        fanout = min(child_count(node), MAX_CHILD_FANOUT)
        for child_index in range(fanout):
            if len(records) >= max_tree_nodes:
                break
            child = child_at(node, child_index)
            visit(child, depth + 1, path + [child_index])

    visit(root, 0, root_path)
    if dropped["count"]:
        lines.append(
            "({} node(s) omitted: {} — raise max_tree_nodes to see them)".format(
                dropped["count"],
                "structural containers with no name, action or value"
                if len(records) < max_tree_nodes
                else "node budget exhausted, remaining subtree not traversed",
            )
        )
    return records, lines


def capture_window_png(bounds):
    if Gdk is None or bounds is None:
        return None
    try:
        screen = Gdk.Screen.get_default()
        if screen is None:
            return None
        root = screen.get_root_window()
        pixbuf = Gdk.pixbuf_get_from_window(
            root,
            int(round(bounds["x"])),
            int(round(bounds["y"])),
            max(1, int(round(bounds["width"]))),
            max(1, int(round(bounds["height"]))),
        )
        if pixbuf is None:
            return None
        if pixbuf_looks_black(pixbuf):
            return None
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        if not ok:
            return None
        return base64.b64encode(bytes(data)).decode("ascii")
    except Exception:
        return None


def pixbuf_looks_black(pixbuf):
    try:
        pixels = pixbuf.get_pixels()
        channels = pixbuf.get_n_channels()
        rowstride = pixbuf.get_rowstride()
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        if width <= 0 or height <= 0 or channels < 3:
            return True
        step_x = max(1, width // 16)
        step_y = max(1, height // 16)
        checked = 0
        for y in range(0, height, step_y):
            row = y * rowstride
            for x in range(0, width, step_x):
                offset = row + (x * channels)
                if (
                    pixels[offset] > 3
                    or pixels[offset + 1] > 3
                    or pixels[offset + 2] > 3
                ):
                    return False
                checked += 1
        return checked > 0
    except Exception:
        return False


def focused_summary(app_pid, text_limit=DEFAULT_TEXT_LIMIT):
    try:
        root = desktop()
        for app in iter_apps():
            if node_pid(app) != app_pid:
                continue
            _, win = main_window(app)
            focused = find_first(
                win, lambda node: state_contains(node, Atspi.StateType.FOCUSED)
            )
            if focused is None:
                return None
            role = node_role(focused)
            name = limit_text(node_name(focused), text_limit=text_limit)
            return (role + " " + name).strip()
    except Exception:
        return None


def selected_text(app_pid, text_limit=DEFAULT_TEXT_LIMIT):
    try:
        for app in iter_apps():
            if node_pid(app) != app_pid:
                continue
            _, win = main_window(app)
            focused = find_first(
                win, lambda node: state_contains(node, Atspi.StateType.FOCUSED)
            )
            if focused is None or not has_text_iface(focused):
                return None
            text_iface = safe(focused.get_text_iface)
            selections = safe(lambda: Atspi.Text.get_text_selections(text_iface), [])
            if selections:
                selection = selections[0]
                end_offset = selection.end_offset
                if text_limit is not None:
                    end_offset = min(end_offset, selection.start_offset + text_limit + 1)
                value = Atspi.Text.get_text(
                    text_iface, selection.start_offset, end_offset
                )
                return limit_text(value, text_limit=text_limit)
    except Exception:
        return None
    return None


def build_snapshot(
    query,
    text_limit=DEFAULT_TEXT_LIMIT,
    max_tree_nodes=MAX_ELEMENTS,
    max_tree_depth=MAX_DEPTH,
    include_screenshot=False,
):
    """构建应用快照。

    `include_screenshot` 默认关闭：a11y 与 VLM 是两条独立轨道，a11y 轨不应该
    顺带付截图的钱。实测 gedit 单次观测里截图占 1014 token（文本 1908），
    约 35%；等树裁剪把文本砍掉后截图会升到 80% 左右，成为主要成本。
    截图改由 `get_screenshot` 显式索取。
    """
    app = resolve_app(query)
    window_index, window = main_window(app)
    bounds = extents(window)
    records, lines = render_tree(
        window,
        bounds,
        [window_index],
        text_limit=text_limit,
        max_tree_nodes=max_tree_nodes,
        max_tree_depth=max_tree_depth,
    )
    pid = node_pid(app)
    return {
        "app": {
            "name": node_name(app),
            "bundleIdentifier": node_name(app),
            "pid": pid,
        },
        "windowTitle": limit_text(node_name(window), text_limit=text_limit),
        "windowBounds": bounds,
        "screenshotPngBase64": capture_window_png(bounds) if include_screenshot else None,
        "treeLines": lines,
        "focusedSummary": focused_summary(pid, text_limit=text_limit),
        "selectedText": selected_text(pid, text_limit=text_limit),
        "elements": records,
    }


def list_apps_text():
    lines = []
    for app in sorted(iter_apps(), key=lambda item: (node_name(item).lower(), node_pid(item))):
        windows = app_windows(app)
        if not windows:
            continue
        title = node_name(windows[0][1]) or "untitled"
        name = node_name(app)
        lines.append(
            "{} -- {} [running, pid={}, window={}]".format(
                name, name, node_pid(app), title
            )
        )
    return "\n".join(lines)


def find_first(root, predicate, budget=None):
    """深度优先找第一个匹配节点，带节点预算与 fanout 上限。

    原实现两者皆无。它被 focused_summary() / selected_text() 用来定位 FOCUSED
    节点，一旦谓词不匹配就会尝试走遍整棵树 —— 在 LibreOffice 上实测约
    9000 节点/秒，推算需要一天以上。

    目前它没出事纯属侥幸：Calc 的 root pane 自带 FOCUSED 位且恰好排在表格
    节点之前。焦点一旦落到别处，这里就会挂死。

    预算耗尽返回 None，与"未找到"语义一致，调用方无需改动。
    """
    if budget is None:
        budget = [FIND_FIRST_BUDGET]
    if root is None or budget[0] <= 0:
        return None
    budget[0] -= 1
    if predicate(root):
        return root
    if not should_enumerate_children(root):
        return None
    for index in range(min(child_count(root), MAX_CHILD_FANOUT)):
        if budget[0] <= 0:
            return None
        found = find_first(child_at(root, index), predicate, budget)
        if found is not None:
            return found
    return None


def iter_all(root):
    items = []

    def visit(node):
        if node is None or len(items) >= MAX_ELEMENTS:
            return
        items.append(node)
        if not should_enumerate_children(node):
            return
        for index in range(min(child_count(node), MAX_CHILD_FANOUT)):
            if len(items) >= MAX_ELEMENTS:
                return
            visit(child_at(node, index))

    visit(root)
    return items


def resolve_path(app, path):
    if not path:
        return None
    node = app
    for index in path:
        node = child_at(node, int(index))
        if node is None:
            return None
    return node


def same_frame(record_frame, node_frame):
    if record_frame is None or node_frame is None:
        return False
    for key in ("x", "y", "width", "height"):
        if abs(float(record_frame.get(key, 0)) - float(node_frame.get(key, 0))) > 3:
            return False
    return True


def find_element(app, record):
    if not record:
        return None
    node = resolve_path(app, record.get("runtimeId") or [])
    if node is not None:
        return node

    _, window = main_window(app)
    target_name = str(record.get("name") or "")
    target_id = str(record.get("automationId") or "")
    target_role = str(record.get("controlType") or "")
    window_bounds = extents(window)
    for candidate in iter_all(window):
        if target_id and accessible_id(candidate) == target_id:
            return candidate
        if target_name and node_name(candidate) == target_name and node_role(candidate) == target_role:
            return candidate
        if target_role and node_role(candidate) == target_role:
            if same_frame(record.get("frame"), relative_frame(candidate, window_bounds)):
                return candidate
    return None


def preferred_action_index(node):
    preferred_exact = {
        "click",
        "press",
        "activate",
        "default.activate",
        "invoke",
        "select",
        "toggle",
        "open",
    }
    count = int(safe(node.get_n_actions, 0) or 0)
    fallback = None
    for index in range(count):
        name = str(safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(safe(lambda i=index: node.get_action_description(i), "") or "")
        lower = (name or description).lower()
        if lower in preferred_exact:
            return index
        if fallback is None and (
            "activate" in lower or "click" in lower or "press" in lower
        ):
            fallback = index
    return fallback


def do_action_by_index(node, index):
    if index is None:
        return False
    return bool(safe(lambda: node.do_action(int(index)), False))


def screen_point(window_bounds, element=None, x=None, y=None):
    if element is not None:
        f = element.get("frame")
        if f is not None and window_bounds is not None:
            return (
                window_bounds["x"] + f["x"] + f["width"] / 2,
                window_bounds["y"] + f["y"] + f["height"] / 2,
            )
    if x is None or y is None or window_bounds is None:
        raise RuntimeError("coordinate action requires window bounds and x/y")
    return window_bounds["x"] + float(x), window_bounds["y"] + float(y)


def mouse_button_events(button):
    normalized = (button or "left").lower()
    if normalized == "right":
        return "b3p", "b3r"
    if normalized == "middle":
        return "b2p", "b2r"
    return "b1p", "b1r"


def window_is_active(window):
    return window is not None and state_contains(window, Atspi.StateType.ACTIVE)


def focus_window(window, timeout=1.0):
    """尽力把目标窗口抬到输入焦点，返回是否成功。

    frame 自身的 Atspi.Component.grab_focus() 在 GTK 上恒返回 False，
    必须找一个 FOCUSABLE 的子控件来抓焦点；窗口内上次获得焦点的控件
    即使在窗口失活时仍保留 FOCUSED 状态，所以优先试它。
    """
    if window is None:
        return False
    if window_is_active(window):
        return True

    def grab(node):
        component = safe(node.get_component_iface)
        if component is None:
            return False
        return bool(safe(lambda: Atspi.Component.grab_focus(component), False))

    focusable = [
        node
        for node in iter_all(window)
        if state_contains(node, Atspi.StateType.FOCUSABLE)
    ]
    focusable.sort(
        key=lambda node: state_contains(node, Atspi.StateType.FOCUSED), reverse=True
    )
    for node in focusable[:FOCUS_GRAB_CANDIDATES]:
        if not grab(node):
            continue
        deadline = time.time() + timeout
        while time.time() < deadline:
            if window_is_active(window):
                return True
            time.sleep(0.05)
    return window_is_active(window)


def require_window_focus(window, what):
    """合成输入前强制确认目标窗口已激活。

    Atspi.generate_keyboard_event / generate_mouse_event 走的是 XTEST 全局
    合成：按键落到当前**输入焦点**窗口、点击落到该屏幕坐标**最上层**窗口，
    都跟工具调用里的 app 参数无关。不先夺焦点就直接合成，内容会打进别的
    应用——最坏情况是本该输入编辑器的文本落进终端并被执行。

    抬窗失败时宁可硬失败，也不要静默把输入送错地方。
    """
    if focus_window(window):
        return
    raise RuntimeError(
        "Refusing to synthesize {}: could not bring the target window to the "
        "foreground. Input synthesis is global and would be delivered to "
        "whichever window currently holds focus.".format(what)
    )


def send_mouse_click(x, y, button, count):
    down, up = mouse_button_events(button)
    repeat = max(1, int(count or 1))
    for _ in range(repeat):
        Atspi.generate_mouse_event(int(round(x)), int(round(y)), "abs")
        Atspi.generate_mouse_event(int(round(x)), int(round(y)), down)
        time.sleep(0.035)
        Atspi.generate_mouse_event(int(round(x)), int(round(y)), up)
        time.sleep(0.05)


def send_drag(from_x, from_y, to_x, to_y):
    Atspi.generate_mouse_event(int(round(from_x)), int(round(from_y)), "abs")
    Atspi.generate_mouse_event(int(round(from_x)), int(round(from_y)), "b1p")
    steps = 12
    for step in range(1, steps + 1):
        x = from_x + ((to_x - from_x) * step / steps)
        y = from_y + ((to_y - from_y) * step / steps)
        Atspi.generate_mouse_event(int(round(x)), int(round(y)), "abs")
        time.sleep(0.02)
    Atspi.generate_mouse_event(int(round(to_x)), int(round(to_y)), "b1r")


KEY_ALIASES = {
    "return": "Return",
    "enter": "Return",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "BackSpace",
    "back_space": "BackSpace",
    "delete": "Delete",
    "space": "space",
    "left": "Left",
    "up": "Up",
    "right": "Right",
    "down": "Down",
    "home": "Home",
    "end": "End",
    "page_up": "Page_Up",
    "prior": "Page_Up",
    "page_down": "Page_Down",
    "next": "Page_Down",
}

MODIFIER_KEYS = {
    "ctrl": "Control_L",
    "control": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "super": "Super_L",
    "win": "Super_L",
    "cmd": "Super_L",
}


def keycode(name):
    """把键名解析成 X11 hardware keycode。

    Atspi.generate_keyboard_event(keyval, keystring, synth_type) 在
    PRESS / RELEASE / PRESSRELEASE 三种模式下，第一个参数要的是
    **hardware keycode**；只有 SYM 模式才接受 keysym。

    传 keysym 进去不会报错，而是被截断到低 8 位，发出完全不相干的键：

        Escape    keysym 65307 -> 截断 27  -> 实际发出 'r'
        Return    keysym 65293 -> 截断 13  -> 实际发出 '4'
        Delete    keysym 65535 -> 截断 255 -> 实际发出 XF86RFKill
        Control_L keysym 65507 -> 截断 227 -> 修饰键根本没按下

    这是静默注入错误动作，比直接报错危险，所以这里解析失败一律抛异常。
    """
    if Gdk is None:
        raise RuntimeError("Gdk unavailable; cannot resolve keycode for: " + name)
    sym = Gdk.keyval_from_name(name)
    if not sym and len(name) == 1:
        sym = ord(name)
    if not sym:
        raise RuntimeError("Unsupported key: " + name)
    keymap = Gdk.Keymap.get_default()
    ok, entries = keymap.get_entries_for_keyval(sym)
    if not ok or not entries:
        raise RuntimeError("No hardware keycode mapped for key: " + name)
    return int(entries[0].keycode)


def parse_key(key):
    """把按键字符串解析成 (修饰键 keycode 列表, 规范化后的主键)，不发送任何事件。

    解析必须能独立于发送先做一遍：夺焦点是有副作用的（会打断用户），
    不该为了一个根本拼错的按键先把窗口抢过来、再报参数错误。
    """
    parts = [part for part in str(key).split("+") if part]
    if not parts:
        raise RuntimeError("Unsupported key: " + str(key))
    main = parts[-1]
    codes = []
    for modifier in parts[:-1]:
        name = MODIFIER_KEYS.get(modifier.lower())
        if name is None:
            # 原实现是静默 continue，会让 "ctrl+shft+s" 这类拼写错误
            # 悄悄退化成孤立的 "s"，行为诡异且极难排查。
            raise RuntimeError("Unsupported modifier: " + modifier)
        codes.append(keycode(name))
    normalized = KEY_ALIASES.get(main.lower(), main)
    # 单字符键只有在**无修饰键**时才能走 STRING。STRING 是"插入这段文本"
    # 的语义，会绕过已经 PRESS 的修饰键状态，使 ctrl+a 退化成输入字面 'a'。
    if not (len(normalized) == 1 and not codes):
        keycode(normalized)  # 提前确认主键可解析，失败就在这里报错
    return codes, normalized


def send_key(key):
    codes, normalized = parse_key(key)
    pressed = []
    try:
        for code in codes:
            Atspi.generate_keyboard_event(code, None, Atspi.KeySynthType.PRESS)
            pressed.append(code)
        if len(normalized) == 1 and not pressed:
            Atspi.generate_keyboard_event(0, normalized, Atspi.KeySynthType.STRING)
        else:
            Atspi.generate_keyboard_event(
                keycode(normalized), None, Atspi.KeySynthType.PRESSRELEASE
            )
    finally:
        # 必须放 finally：主键解析失败时若不释放，修饰键会永久卡在按下状态，
        # 之后所有输入都会带上这个修饰键。
        for code in reversed(pressed):
            Atspi.generate_keyboard_event(code, None, Atspi.KeySynthType.RELEASE)


def send_text(text):
    Atspi.generate_keyboard_event(0, str(text), Atspi.KeySynthType.STRING)


def find_editable_text(root):
    """挑出真正该接收文本的可编辑控件。

    不能只看 EditableText 接口存不存在就取树序第一个。gedit 这类应用里，
    树序靠前的往往是隐藏的占位控件：它实现了 EditableText 接口，却没有
    EDITABLE 状态。对它调 Atspi.EditableText.insert_text() **照样返回 True**，
    但字符数不变——文本静默写丢，工具却报成功。

    因此这里同时要求 EDITABLE 状态，并按 FOCUSED > SHOWING > 其它排序，
    优先写进用户当前正在编辑的控件。
    """
    candidates = []
    for node in iter_all(root):
        if not (has_editable_text_iface(node) and has_text_iface(node)):
            continue
        if not state_contains(node, Atspi.StateType.EDITABLE):
            continue
        if state_contains(node, Atspi.StateType.FOCUSED):
            rank = 2
        elif state_contains(node, Atspi.StateType.SHOWING):
            rank = 1
        else:
            rank = 0
        candidates.append((rank, node))
    if not candidates:
        return None
    # 只按 rank 排序：Accessible 之间不可比较，key 保证不会去比第二个元素。
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def read_all_text(text_iface):
    """读出控件当前的全部文本；读不到返回 None（用来区分"空"和"读不到"）。"""
    if text_iface is None:
        return None
    count = safe(lambda: Atspi.Text.get_character_count(text_iface))
    if count is None:
        return None
    count = int(count)
    if count <= 0:
        return ""
    return safe(lambda: Atspi.Text.get_text(text_iface, 0, count))


def text_insertion_point(text_iface):
    """决定文本该插到哪里，返回 (offset, selection)。

    优先级：非空选区起点 > caret > 末尾追加。selection 非空时调用方要先把它
    删掉——真的在键盘上打字会覆盖选中内容，`type_text` 应该保持一致。

    只有读不到 caret 时才退回末尾追加（部分控件不实现 caret 查询）。
    """
    count = safe(lambda: Atspi.Text.get_character_count(text_iface))
    count = int(count) if count is not None else 0

    selections = int(safe(lambda: Atspi.Text.get_n_selections(text_iface), 0) or 0)
    for index in range(max(selections, 0)):
        span = safe(lambda index=index: Atspi.Text.get_selection(text_iface, index))
        if span is None:
            continue
        start = int(safe(lambda: span.start_offset, 0) or 0)
        end = int(safe(lambda: span.end_offset, 0) or 0)
        if end > start:
            return start, (start, end)

    caret = safe(lambda: Atspi.Text.get_caret_offset(text_iface))
    if caret is not None and 0 <= int(caret) <= count:
        return int(caret), None
    return count, None


def insert_text_detail(root, text):
    """通过 AT-SPI 直写文本，返回 (是否落地, 写前字符数, 写后字符数)。

    调用方需要这三个值来如实报告"写进去了、并且确认过"，而不是只说一句成功。
    """
    node = find_editable_text(root)
    if node is None:
        return False, 0, 0
    editable = safe(node.get_editable_text_iface)
    text_iface = safe(node.get_text_iface)
    if editable is None or text_iface is None:
        return False, 0, 0
    payload = str(text)
    if not payload:
        count = int(safe(lambda: Atspi.Text.get_character_count(text_iface), 0) or 0)
        return True, count, count

    offset, selection = text_insertion_point(text_iface)
    if selection is not None:
        safe(
            lambda: Atspi.EditableText.delete_text(editable, selection[0], selection[1])
        )

    # 删掉选区会改变字符数，增长判定的基准必须在删除之后再读一次。
    before = safe(lambda: Atspi.Text.get_character_count(text_iface))
    before = int(before) if before is not None else 0
    offset = max(0, min(offset, before))

    if not safe(
        lambda: Atspi.EditableText.insert_text(editable, offset, payload, len(payload)),
        False,
    ):
        return False, before, before
    # insert_text 的返回值不可信：对错误的控件也会返回 True。回读字符数确认
    # 文本真的落地了，否则返回 False 让调用方走合成兜底，而不是假装成功。
    after = safe(lambda: Atspi.Text.get_character_count(text_iface))
    if after is None:
        return False, before, before
    return int(after) > before, before, int(after)


def insert_text(root, text):
    written, _, _ = insert_text_detail(root, text)
    return written


def set_element_value(node, value):
    if node is not None and has_editable_text_iface(node):
        editable = safe(node.get_editable_text_iface)
        if editable is not None:
            payload = str(value)
            text_iface = safe(node.get_text_iface)
            before = read_all_text(text_iface)
            if not safe(
                lambda: Atspi.EditableText.set_text_contents(editable, payload),
                False,
            ):
                return False
            # set_text_contents 和 insert_text 一样，对写不进去的控件同样返回
            # True。只要还能读回来就回读确认：内容变成目标值，或者至少发生了
            # 变化（部分控件会规范化输入），都算写进去了；纹丝不动且不等于目标
            # 值，就是没写进去。
            after = read_all_text(text_iface)
            if after is None:
                return True
            return after == payload or after != before
    value_iface = safe(node.get_value_iface) if node is not None else None
    if value_iface is not None:
        try:
            return bool(Atspi.Value.set_current_value(value_iface, float(value)))
        except Exception:
            pass
    return False


def invoke_secondary_action(node, action):
    if node is None:
        raise RuntimeError("unknown element_index")
    normalized = str(action).lower()
    count = int(safe(node.get_n_actions, 0) or 0)
    for index in range(count):
        name = str(safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(safe(lambda i=index: node.get_action_description(i), "") or "")
        if normalized in {name.lower(), description.lower()}:
            if do_action_by_index(node, index):
                return
            break
    raise RuntimeError("{} is not a valid secondary action for element".format(action))


def scroll_element(direction, pages):
    key = "Page_Down"
    if direction == "up":
        key = "Page_Up"
    elif direction == "left":
        key = "Left"
    elif direction == "right":
        key = "Right"
    repeat = max(1, int(math.ceil(float(pages or 1))))
    for _ in range(repeat):
        send_key(key)
        time.sleep(0.04)


# 动作走的是哪条通道。加前缀有两个目的：
# 1. 让"语义调用 vs 坐标兜底"的比例可以被机器统计——这是 plan 里 S3 报告口径的
#    第四项，也是区分"agent 不想用 a11y"与"用了但失败后退化"的唯一依据。
# 2. 让 agent 一眼看出自己刚才走的是主通道还是兜底通道。
SEMANTIC = "[semantic] "
SYNTHESIS = "[synthesis] "

UNVERIFIED_SYNTHESIS = (
    "Delivery to the intended target was not verified: AT-SPI input synthesis is "
    "global and reports success as soon as the event is queued."
)

# 走了坐标兜底时的即时纠偏。只在元素定向本可用时才有意义，所以由调用方决定是否附加。
PREFER_ELEMENT_INDEX = (
    " This did not use element-targeted invocation. If the target appears in the "
    "accessibility tree, prefer click(element_index=...) — it is verified, cheaper, "
    "and does not steal focus."
)


def snapshot_diagnostics(records):
    """检测"应用活着、窗口也在，但 a11y 是空壳"这种状态。

    Chromium / Electron 默认不为渲染进程生成 a11y 树，snap 封装的应用接不上
    accessibility 总线——两种情况下应用都正常运行、窗口标题也正常，但整棵树里
    只有一个窗口框。此时返回 isError=false 且不加说明，agent 只会以为"这个窗口
    是空的"，然后在一个它根本看不见的应用上反复试错。

    这里不报错而是给诊断：窗口确实可能合法地为空（空白对话框），报错会误伤；
    但必须让 agent 能分辨"界面为空"和"我看不见这个界面"，这也是切到 VLM 通道的信号。
    """
    if not records:
        return []
    for record in records:
        role = str(record.get("controlType") or "").lower()
        if role in {"frame", "window", "dialog", "alert", "application"}:
            continue
        if record.get("name") or record.get("actions") or record.get("value"):
            return []
    return [
        "This app is running and has a window, but exposes no accessibility content "
        "beyond the window frame itself ({} element(s) total). Element-targeted actions "
        "cannot work here, and an empty tree does NOT mean the window is empty. Common "
        "causes: Chromium-based apps (Chrome, Electron, VS Code) need "
        "--force-renderer-accessibility; snap-packaged apps usually cannot reach the "
        "accessibility bus at all. Either relaunch the app with accessibility enabled, "
        "or switch to the screenshot/coordinate path for this app.".format(len(records))
    ]


def perform_operation(operation):
    tool = operation.get("tool")
    if tool == "list_apps":
        return {"ok": True, "text": list_apps_text()}
    if tool == "get_screenshot":
        # VLM 轨道的唯一入口。树只渲染一层，避免为了拿一张图顺带付整棵树的钱。
        return {
            "ok": True,
            "snapshot": build_snapshot(
                operation.get("app", ""),
                max_tree_nodes=1,
                max_tree_depth=1,
                include_screenshot=True,
            ),
        }
    if tool == "get_app_state":
        snapshot = build_snapshot(
            operation.get("app", ""),
            text_limit=parse_text_limit(operation.get("text_limit"), DEFAULT_TEXT_LIMIT),
            max_tree_nodes=positive_int(operation.get("max_tree_nodes"), MAX_ELEMENTS),
            max_tree_depth=positive_int(operation.get("max_tree_depth"), MAX_DEPTH),
        )
        response = {"ok": True, "snapshot": snapshot}
        diagnostics = snapshot_diagnostics(snapshot.get("elements") or [])
        if diagnostics:
            response["notes"] = diagnostics
        return response

    app = resolve_app(operation.get("app", ""))
    _, window = main_window(app)
    bounds = operation.get("windowBounds") or extents(window)
    element_record = operation.get("element")
    element = find_element(app, element_record)
    # 每个动作都要说清楚实际走了哪条路径、结果有没有被校验过。返回一棵新的
    # accessibility tree 看着像执行确认，其实只是快照，不能当成动作生效的证据。
    notes = []

    if tool == "click":
        click_method = (operation.get("click_method") or "auto").lower()
        if click_method == "accessibility":
            if element is None:
                raise RuntimeError("click_method 'accessibility' requires element_index")
            if operation.get("mouse_button", "left") != "left":
                raise RuntimeError(
                    "click_method 'accessibility' only supports mouse_button 'left'"
                )
            if not do_action_by_index(element, preferred_action_index(element)):
                raise RuntimeError(
                    "click_method 'accessibility' could not click the requested element"
                )
            notes.append(SEMANTIC + "Invoked the element's AT-SPI accessibility action.")
        elif click_method == "app_post":
            raise RuntimeError("click_method 'app_post' is not supported on Linux")
        elif click_method == "sky_click":
            raise RuntimeError("click_method 'sky_click' is not supported on Linux")
        elif click_method == "global":
            x, y = screen_point(
                bounds,
                element_record,
                operation.get("x"),
                operation.get("y"),
            )
            require_window_focus(window, "click")
            send_mouse_click(
                x, y, operation.get("mouse_button", "left"), operation.get("click_count", 1)
            )
            notes.append(
                SYNTHESIS
                + "Synthesized a coordinate click at ({:.0f}, {:.0f}) after bringing the "
                "window to the foreground. {}{}".format(
                    x, y, UNVERIFIED_SYNTHESIS, PREFER_ELEMENT_INDEX
                )
            )
        elif click_method == "auto":
            handled = False
            if element is not None and operation.get("mouse_button", "left") == "left":
                handled = do_action_by_index(element, preferred_action_index(element))
            if handled:
                notes.append("Invoked the element's AT-SPI accessibility action.")
            else:
                x, y = screen_point(
                    bounds,
                    element_record,
                    operation.get("x"),
                    operation.get("y"),
                )
                require_window_focus(window, "click")
                send_mouse_click(
                    x,
                    y,
                    operation.get("mouse_button", "left"),
                    operation.get("click_count", 1),
                )
                notes.append(
                    SYNTHESIS
                    + "No usable AT-SPI action was available, so this fell back to a "
                    "coordinate click at ({:.0f}, {:.0f}) after bringing the window to "
                    "the foreground. {}".format(x, y, UNVERIFIED_SYNTHESIS)
                )
        else:
            raise RuntimeError("Invalid click_method '{}'".format(click_method))
    elif tool == "perform_secondary_action":
        invoke_secondary_action(element, operation.get("action", ""))
        notes.append(
            SEMANTIC + "Invoked the '{}' AT-SPI action.".format(operation.get("action", ""))
        )
    elif tool == "scroll":
        require_window_focus(window, "scroll")
        scroll_element(operation.get("direction", "down"), operation.get("pages", 1))
        notes.append(
            SYNTHESIS
            + "Scrolled by synthesizing page keys after bringing the window to the "
            "foreground. {}".format(UNVERIFIED_SYNTHESIS)
        )
    elif tool == "drag":
        from_x, from_y = screen_point(
            bounds, None, operation.get("from_x"), operation.get("from_y")
        )
        to_x, to_y = screen_point(bounds, None, operation.get("to_x"), operation.get("to_y"))
        require_window_focus(window, "drag")
        send_drag(from_x, from_y, to_x, to_y)
        notes.append(
            SYNTHESIS
            + "Synthesized a coordinate drag after bringing the window to the "
            "foreground. {}".format(UNVERIFIED_SYNTHESIS)
        )
    elif tool == "type_text":
        # AT-SPI 直写不依赖窗口焦点，优先走；只有退化到全局合成时才需要夺焦点。
        written, before_chars, after_chars = insert_text_detail(
            window, operation.get("text", "")
        )
        if written:
            notes.append(
                SEMANTIC
                + "Wrote the text through the AT-SPI editable-text API and confirmed it "
                "landed ({} -> {} characters).".format(before_chars, after_chars)
            )
        else:
            require_window_focus(window, "type_text")
            send_text(operation.get("text", ""))
            notes.append(
                SYNTHESIS
                + "The AT-SPI editable-text write did not land, so this fell back to "
                "global key synthesis after bringing the window to the foreground. "
                "{}".format(UNVERIFIED_SYNTHESIS)
            )
    elif tool == "press_key":
        # 先解析再夺焦点：拼错的按键不该先把用户的窗口抢过来才报错。
        parse_key(operation.get("key", ""))
        require_window_focus(window, "press_key")
        send_key(operation.get("key", ""))
        notes.append(
            SYNTHESIS
            + "Synthesized '{}' after bringing the window to the foreground. {}".format(
                operation.get("key", ""), UNVERIFIED_SYNTHESIS
            )
        )
    elif tool == "set_value":
        if element is None:
            raise RuntimeError("unknown element_index")
        if not set_element_value(element, operation.get("value", "")):
            raise RuntimeError("Cannot set a value for an element that is not settable")
        notes.append(SEMANTIC + "Set the value through the AT-SPI API and confirmed it applied.")
    else:
        raise RuntimeError('unsupportedTool("{}")'.format(tool))

    time.sleep(0.12)
    response = {"ok": True, "snapshot": build_snapshot(operation.get("app", ""))}
    if notes:
        response["notes"] = notes
    return response


def main():
    if len(sys.argv) != 2:
        raise RuntimeError("runtime.py requires an operation JSON path")
    require_desktop_session()
    Atspi.init()
    with open(sys.argv[1], "r", encoding="utf-8") as file:
        operation = json.load(file)
    try:
        response = perform_operation(operation)
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    print(json.dumps(response, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"ok": False, "error": traceback.format_exc()}))
