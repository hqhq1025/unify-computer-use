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

# 裁剪用的角色判据。与 OSWorld 官方 `judge_node()` 同源
# （mm_agents/accessibility_tree_wrap/heuristic_retrieve.py）：前缀/后缀匹配
# 加一个精确集合，再叠加可见性要求。
#
# 离线评测（13 步轨迹 / 9 步元素定向，含完整对话框链路）显示这套判据能压到
# 22% 且保留率 100%——是所有候选里唯一同时最激进且无损的。前提是渲染保真度
# 到位：单元格要带 Frame、角色不能硬编码，两者都已修。
PRUNE_ROLE_SUFFIXES = (
    "item", "button", "heading", "label", "scrollbar", "searchbox", "textbox",
    "link", "tabelement", "textfield", "textarea", "menu",
)
PRUNE_ROLE_EXACT = {
    "alert", "canvas", "check box", "combo box", "entry", "icon", "image",
    "paragraph", "scroll bar", "section", "slider", "static", "table cell",
    "terminal", "text", "table", "list box", "tree", "tree item", "list item",
    "page tab", "page tab list", "spin button", "tool bar", "status bar",
    "frame", "dialog", "window",
}


def is_interactive_role(role):
    """角色是否属于"agent 可能需要操作或读取"的一类。"""
    normalized = role.lower().strip()
    if normalized.startswith("document"):
        return True
    compact = normalized.replace(" ", "")
    if compact.endswith(PRUNE_ROLE_SUFFIXES):
        return True
    return normalized in PRUNE_ROLE_EXACT


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


# Chromium/Electron 在文本里用 U+FFFC（对象替换符）给嵌入对象占位。
# 它对 agent 零信息量，却会渲染成 `Value: ￼￼￼` 这样的乱码。
# VS Code 欢迎页实测：186 个占位符散布在 115 行里，纯占位的 Value 段
# 合计约 247 token——不多，但它更严重的问题是**看起来像内容**：
# 一个 `Value: ￼` 会让 agent 以为这个控件有值。
OBJECT_REPLACEMENT = "\ufffc"


def limit_text(value, text_limit=DEFAULT_TEXT_LIMIT):
    text = str(value or "").replace(OBJECT_REPLACEMENT, "")
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

# 声明了 MANAGES_DESCENDANTS 但自报子节点数不超过这个值时，仍然直接枚举。
# 该状态是"我可能很大，别硬枚举"的提示，不是"我一定很大"的事实：
# Nautilus 的侧边栏就声明了它，却只有 12 个子节点，而且**不实现 Table 接口**，
# 于是坐标寻址兜底必然失败——结果整个侧边栏对 agent 不可见。
# 真正的危险案例（Calc 的 sheet 自报 21 亿）会被 HARD_CHILD_CAP 拦住，
# 不依赖这个阈值。
MANAGED_ENUMERATE_CAP = int(os.environ.get("OPEN_COMPUTER_USE_MANAGED_CAP", "256"))

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
    # Chromium/Electron 的默认动作名。VS Code 实测：19 个节点的动作表是
    # ('doDefault', 'showContextMenu')——`doDefault` 就是它们的点击入口。
    # 不认这个名字的话，这些元素既拿不到 [has-click-action] 标记，
    # click_method "accessibility" 也会直接失败，整个 Electron 系应用
    # 在语义通道上等于不可点。
    "dodefault",
}

# 名字里含 click/press/activate，但**作用对象不是这个元素本身**的动作。
# 必须显式排除：`preferred_action_index()` 的兜底是子串匹配，
# `clickAncestor` 会被它匹中，于是 agent 以为点中了目标，
# 实际点的是祖先节点——而且从返回值和树里都看不出来。
# VS Code 实测有 14 个节点的动作表是 ('clickAncestor', 'showContextMenu')。
NON_SELF_ACTIONS = {"clickancestor"}

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
    count = child_count(node)
    if count > HARD_CHILD_CAP:
        return False
    if state_contains(node, Atspi.StateType.MANAGES_DESCENDANTS):
        # 自管理声明是关于**规模**的提示。自报数量很小时它与声明自相矛盾，
        # 此时按数量走：枚举 12 个子节点既安全又是拿到内容的唯一办法
        # （这类容器往往不实现 Table，坐标寻址兜底根本用不上）。
        return count <= MANAGED_ENUMERATE_CAP
    return True


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
    """按名字/标题/pid 找应用，找不到时**重试几次**再宣告失败。

    实测（LibreOffice 7.3）：`get_app_state` 刚成功，紧接着同一会话里的 `click`
    就报 `appNotFound("soffice")`，而 AT-SPI 桌面里那一条自始至终都在。
    三次失败都发生在**会开关对话框的点击之后**——应用正忙于重建窗口时，
    枚举链路上任何一环（`Atspi.get_desktop`、`get_child_count`、`get_name`）
    瞬时读失败，都会被 `safe()` 吞成"这个应用不存在"。

    没能钉死具体是哪一环（空名字与桌面条目消失两种猜想都实测证伪了），
    但无论哪种瞬时故障，处置都一样：**"暂时读不到"不等于"不存在"**。
    直接抛 appNotFound 是在向 agent 谎报事实——它会据此改用别的应用名、
    重启应用，甚至判定任务无法完成，而真相只是需要再读一次。

    重试上限很小：应用真的不存在时不该让调用方多等。
    """
    last_error = None
    for attempt in range(RESOLVE_APP_ATTEMPTS):
        try:
            for app in iter_apps():
                if matches_query(app, query):
                    return app
        except Exception as error:      # 枚举本身炸了也算这一轮没找到
            last_error = error
        if attempt + 1 < RESOLVE_APP_ATTEMPTS:
            time.sleep(RESOLVE_APP_RETRY_SECONDS)
    if last_error is not None:
        raise RuntimeError(
            'appNotFound("{}") after {} attempts (last enumeration error: {})'.format(
                query, RESOLVE_APP_ATTEMPTS, last_error
            )
        )
    raise RuntimeError('appNotFound("{}")'.format(query))


def node_actions(node):
    """**一次读完**该节点的动作表，同时给出渲染需要的两个结论。

    返回 `(未被 click 覆盖的动作名, 是否存在可调用的 click 入口)`。

    为什么必须合成一次读：LibreOffice 的 ATK 桥在被反复问动作表时会打出

        (soffice): CRITICAL: impl_get_NActions:
                   assertion 'ATK_IS_ACTION (user_data)' failed

    密集调用下**应用会整个退出**（实测：对话框循环第 3~4 轮 soffice 消失，
    随后 get_app_state 报 appNotFound）。渲染一棵树本来每个节点就要问一次，
    早先为了给出 `[has-click-action]` 标记又经 preferred_action_index() 问了
    第二次——等于在一个已知脆弱的桥上把压力翻倍。

    动作名与描述在同一轮里取，因此每个节点对 ATK 的问询次数减半。
    """
    # 先确认这个节点真的实现了 Action 接口再问动作数。
    #
    # 不先问就直接调 get_n_actions()，LibreOffice 的 ATK 桥会对着一个不是
    # ATK_ACTION 的对象打断言：
    #     CRITICAL: impl_get_NActions: assertion 'ATK_IS_ACTION (user_data)' failed
    # 而树里绝大多数节点（panel / filler / label / 各种容器）本来就没有这个接口，
    # 于是每渲染一棵树就刷出成百上千条。实测一轮对话框循环刷出 3482 条。
    if safe(node.get_action_iface) is None:
        return [], False
    count = int(safe(node.get_n_actions, 0) or 0)
    names = []
    has_click_entry = False
    for index in range(count):
        name = str(safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(
            safe(lambda i=index: node.get_action_description(i), "") or ""
        )
        label = name or description
        lower = label.strip().lower()
        # 与 preferred_action_index() 用同一套判据：精确命中，或含
        # activate/click/press 的兜底。两者必须一致，否则标记会撒谎。
        if lower in CLICK_COVERED_ACTIONS or (
            lower not in NON_SELF_ACTIONS
            and ("activate" in lower or "click" in lower or "press" in lower)
        ):
            has_click_entry = True
        if not label or label in names:
            continue
        if lower in CLICK_COVERED_ACTIONS:
            continue
        names.append(label)
    return names, has_click_entry


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
    return node_actions(node)[0]


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


def node_description(node, text_limit=DEFAULT_TEXT_LIMIT):
    """取控件的描述文本（AT-SPI `get_description`，GTK 通常填的是 tooltip）。

    实测 Nautilus：工具栏上 `Go back` / `Go forward` / `Search` / `Show list`
    四个按钮**名字全是空的**，唯一的可读标识就在 description 里；而三个
    `toggle button Menu` 名字完全相同，只有 description（`Show operations` /
    `View options`）能区分。不渲染这一项，返回/前进这类文件管理器核心操作
    在树里就只是 `push button`，agent 除了按像素坐标猜没有别的办法——
    a11y 优先的整条路径在这里断掉。

    GTK 的惯例与 macOS 相反：macOS 把可读标签放 AXTitle，GTK 常常只填
    tooltip。所以这不是"移植时漏了"，是 Linux 侧必须多读一处。

    与 name 分开渲染而不是顶替它：name 是元素身份的一部分（轨迹回放、
    裁剪保留率都按 `role + name` 匹配），改写它会让同一个元素在不同版本里
    对不上号。
    """
    return limit_text(str(safe(node.get_description, "") or ""), text_limit=text_limit)


def placeholder_text(node, text_limit=DEFAULT_TEXT_LIMIT):
    """取控件的占位提示文本（AT-SPI 的 `placeholder-text` 对象属性）。

    对齐 macOS 的 `placeholderValue`。必须和真实内容分开渲染：占位文本长得
    像内容但其实是空的，混在一起 agent 会以为输入框已经有值、跳过输入，
    或者把提示语当成数据读走。
    """
    attributes = safe(node.get_attributes) or {}
    for key in ("placeholder-text", "placeholder"):
        value = attributes.get(key)
        if value:
            return limit_text(str(value), text_limit=text_limit)
    return ""


def state_segment(node, has_click_action=False):
    """把值得关注的状态渲染成紧凑标记，如 `[checked expanded]`。

    **只报告"存在即有意义"的状态，绝不从状态的缺失反推语义。**

    这条规则是踩坑换来的：最初这里会在缺少 `ENABLED` 时标 `[disabled]`，
    实测发现 Nautilus 的文件图标根本不设 `ENABLED` / `SENSITIVE`——
    它们状态里只有 `SHOWING, VISIBLE, FOCUSABLE`，却带着 `open` / `menu` 两个动作，
    完全可操作。被误标成 disabled 之后，agent 会直接跳过真正的目标。

    对照组同样说明问题：gedit 那个被我一度当成"禁用"的 `Clear Highlight`，
    其实是**没有 SHOWING**（藏在折叠的搜索面板里），不是禁用。

    结论：AT-SPI 的 ENABLED/SENSITIVE 在 Linux 上不可靠，不同工具包设不设全凭自觉，
    没有可靠判据能区分"真禁用"与"这个工具包不上报"。宁可少给一个信号，
    也不能给一个会让 agent 跳过可用目标的假信号。
    """
    marks = []
    for name, label in NOTABLE_STATES:
        state = getattr(Atspi.StateType, name, None)
        if state is not None and state_contains(node, state):
            marks.append(label)
    # `clickable` 不是 AT-SPI 状态，是一条**能力**判断，但它属于同一个括号组：
    # agent 扫一眼就该知道这个元素能不能直接按元素点。
    #
    # 为什么必须显式标出来：action_names() 会隐藏 click 类动作（否则每个可点节点
    # 都显示 "More actions: click"，模型会以为那是 click 工具之外的另一条路，
    # 于是在两个工具之间反复摇摆）。但一并隐藏之后，"有语义点击"和"根本没有动作
    # 接口"在树里长得**一模一样**：
    #     3  push button Home              <- 有 click 动作，可直接调
    #     12 list item  Recent files       <- 没有 Action 接口，只能坐标点
    # agent 无从分辨，只好一律退回坐标——恰好背离 a11y 优先。
    #
    # 实测这是少数派（Nautilus 10 个可点 vs 103 个不可点，gedit 7 vs 30），
    # 所以标可点的比标不可点的省一个数量级。
    #
    # 判据必须与 click 工具实际调用的入口同源（preferred_action_index），
    # 否则这个标记本身就成了新的谎言。
    if has_click_action:
        # 措辞刻意是"有一个动作"而不是"点得动"。它保证的是 click 工具能在这个
        # 元素上找到可调用的动作，**不保证那个动作生效**——实测 Nautilus /
        # GIMP / VLC 三个应用的动作都会返回成功却什么都不做。
        # 叫 `clickable` 会被读成"点这里就行"，那就是工具在替 agent 打包票。
        marks.append("has-click-action")
    if not marks:
        return ""
    return " [" + " ".join(marks) + "]"


def record_for(node, index, path, window_bounds, text_limit=DEFAULT_TEXT_LIMIT):
    bounds = relative_frame(node, window_bounds)
    role = node_role(node)
    # 动作表只问一次，两个字段共用——见 node_actions() 里关于 LibreOffice
    # ATK 桥的说明。
    actions, has_click_action = node_actions(node)
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
        "actions": actions,
        "states": state_segment(node, has_click_action=has_click_action),
        "description": node_description(node, text_limit=text_limit),
        "placeholder": placeholder_text(node, text_limit=text_limit),
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
    empty = 0
    for row in range(row0, row1 + 1):
        for col in range(col0, col1 + 1):
            if count >= budget:
                break
            cell = safe(lambda r=row, c=col: Atspi.Table.get_accessible_at(table, r, c))
            if cell is None:
                continue
            # 只取文本，不回退到 numeric_value：Calc 的空单元格 Value 接口
            # 返回 0.0，会让空白单元格看起来像是填了 0 —— 对"找出空单元格"
            # 这类任务是致命的误导。
            value = text_value(cell, text_limit=text_limit)
            if not value:
                # 空单元格不进树。实测一张只有 3 列 4 行数据的表：视口里
                # 1081 个单元格中 1069 个是空的，占掉 19971 / 23182 token（86%）。
                # 空格子除了坐标不携带任何信息，而坐标本身就是 RxCy，
                # 从下面那行范围说明里可以直接推出来。
                #
                # 代价是空单元格拿不到 element_index。范围说明里给出的替代
                # 路径是**实测过的那一条**：用 press_key 把单元格光标移过去，
                # 再 type_text，内容会落进当前光标所在的格子。
                #
                # 名称框（树里的 `text Value: A1`）看起来更直接，但**没验证通过**：
                # set_value 能改它的文本却不触发跳转（控件变了、应用没照做，
                # 与下拉提交同一族），而 click 也没能让它获得键盘焦点。
                # 没验证通过的操作不写进给 agent 的提示里。
                empty += 1
                continue
            index = len(records)
            record = record_for(
                cell, index, path + [index], window_bounds, text_limit=text_limit
            )
            records.append(record)
            # 必须带上 Frame。这些单元格是**屏幕上真实可见**的（它们正是坐标
            # 寻址取到的当前视口内容），漏掉 Frame 会让任何"只保留可见节点"的
            # 裁剪把整个下拉/表格内容判成不可见并全部丢掉——实测中行距下拉的
            # `cell R3C0 Double` 就是这么被三种裁剪策略同时丢掉的，
            # 而它恰恰是任务必须点中的那个元素。
            cell_frame = record.get("frame")
            frame_segment = ""
            if cell_frame is not None:
                frame_segment = " Frame: {{x: {0}, y: {1}, width: {2}, height: {3}}}".format(
                    round(cell_frame["x"]),
                    round(cell_frame["y"]),
                    round(cell_frame["width"]),
                    round(cell_frame["height"]),
                )
            # 用节点真实的 AT-SPI 角色，不要硬编码成 "cell"。角色保真度会影响
            # 任何按角色做的裁剪：OSWorld 官方白名单里有 table-cell 却没有 cell，
            # 实测中整批下拉选项就是因为这个不保真被判成无关角色丢掉的。
            cell_role = record["localizedControlType"] or record["controlType"] or "table cell"
            lines.append(
                ("\t" * (depth + 2))
                + "{} {} R{}C{} {}{}".format(
                    index, cell_role, row, col, value, frame_segment
                ).rstrip()
            )
            count += 1
        if count >= budget:
            break

    if count or empty:
        lines.append(
            ("\t" * (depth + 2))
            + "(rows {}-{} x cols {}-{} are in view: {} non-empty cell(s) listed, "
              "{} empty cell(s) omitted; table is {}x{}. Omitted cells have no "
              "element_index. To put content in one, move the cell cursor there "
              "with press_key and then type_text — typing lands in whichever cell "
              "currently holds the cursor.)".format(
                  row0, row1, col0, col1, count, empty,
                  safe(lambda: Atspi.Table.get_n_rows(table), "?"),
                  safe(lambda: Atspi.Table.get_n_columns(table), "?"),
              )
        )
    return count


def render_tree(root, window_bounds, root_path, text_limit=DEFAULT_TEXT_LIMIT,
                max_tree_nodes=MAX_ELEMENTS, max_tree_depth=MAX_DEPTH, prune=True):
    records = []
    lines = []

    dropped = {"count": 0}
    pressure_at = max(1, int(max_tree_nodes * BUDGET_PRESSURE_RATIO))

    def is_structural_filler(record):
        """无名、无描述、无动作、无值的纯容器。对 agent 没有可操作价值。"""
        return not (
            record["name"]
            or record["description"]
            or record["actions"]
            or record["value"]
        )

    def visit(node, depth, path, render_depth=0):
        """depth 用于遍历预算，render_depth 用于缩进。

        两者必须分开：被裁掉的节点仍会递归子节点，若沿用 depth 做缩进，
        子节点会带着父节点的层级出现，树里就凭空多出空档——实测 Nautilus 上
        缩进会从第 1 层直接跳到第 6 层，读起来像断掉的树。
        render_depth 只在节点**真的被渲染**时才加一。
        """
        if len(records) >= max_tree_nodes or depth > max_tree_depth or node is None:
            if node is not None and len(records) >= max_tree_nodes:
                dropped["count"] += 1
            return
        index = len(records)
        record = record_for(node, index, path, window_bounds, text_limit=text_limit)

        # 裁剪：只保留"可操作角色 + 屏幕上可见"的节点。与 OSWorld 官方判据同源，
        # 实测 22% 压缩率、100% 保留率。被裁的只是它自己这一行，**仍然继续递归
        # 子节点**——中间容器往往正是有价值控件的父节点，连子树一起砍会适得其反。
        if prune and depth > 0:
            role_name = record["controlType"] or ""
            # 有名字的可见节点一律保留，哪怕角色不"可交互"。
            # 实测教训：行距 combo 的 toggle button 本身没有名字，agent 只能靠
            # 父节点 `panel Line Spacing` 指认它。纯按角色白名单裁掉这个 panel，
            # 目标元素虽然还在树里，却**没法被指认**——整条对话框链路当场断掉。
            # 保留率指标只看"目标在不在"，看不到这一层，是它的盲区。
            keeps = record["frame"] is not None and (
                is_interactive_role(role_name)
                or bool(record["name"])
                or bool(record["description"])
            )
            if not keeps:
                dropped["count"] += 1
                for child_index in range(min(child_count(node), MAX_CHILD_FANOUT)):
                    # 本节点没渲染，render_depth 不推进，子节点顶替它的位置
                    visit(child_at(node, child_index), depth + 1,
                          path + [child_index], render_depth)
                return

        # 预算吃紧时优先保住有名字/有动作/有值的节点。丢容器只丢它自己这一行，
        # 仍然继续递归子节点——被丢的容器往往正是有价值控件的父节点。
        if len(records) >= pressure_at and is_structural_filler(record) and depth > 0:
            dropped["count"] += 1
            for child_index in range(min(child_count(node), MAX_CHILD_FANOUT)):
                visit(child_at(node, child_index), depth + 1,
                      path + [child_index], render_depth)
            return

        records.append(record)

        role = record["localizedControlType"] or record["controlType"] or "element"
        title = record["name"] or record["automationId"] or ""
        value_segment = ""
        if record["value"] and record["value"] != title:
            safe_value = record["value"].replace("\r", "\\r").replace("\n", "\\n")
            value_segment = " Value: " + safe_value
        state_seg = record.get("states", "")
        description_seg = ""
        if record.get("description") and record["description"] != title:
            # 单独标注，不并进 name：name 是元素身份的一部分（轨迹按 role+name
            # 匹配）。GTK 把可读标签放在这里的情况很常见，尤其是纯图标按钮。
            description_seg = " Description: " + record["description"]
        placeholder_seg = ""
        if record.get("placeholder") and record["placeholder"] != title:
            # 单独标注，不要混进 Value——它是提示不是内容，控件其实是空的
            placeholder_seg = " Placeholder: " + record["placeholder"]
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
            ("\t" * (render_depth + 1))
            + "{} {} {}{}{}{}{}{}{}".format(
                index, role, title, state_seg, value_segment, description_seg,
                placeholder_seg, actions_segment, frame_segment
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
                    ("\t" * (render_depth + 2))
                    + "({} items collapsed; activate this menu to expand)".format(pending)
                )
            return

        if not should_enumerate_children(node):
            # 超大/自管理容器不能枚举，改用坐标寻址取当前视口内的单元格。
            rendered = render_visible_cells(
                node, render_depth, path, window_bounds, records, lines,
                text_limit=text_limit, max_tree_nodes=max_tree_nodes,
            )
            if rendered == 0:
                # 寻址也失败时显式说明，避免模型以为这里本来就是空的。
                lines.append(
                    ("\t" * (render_depth + 2))
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
            # 本节点渲染出来了，子节点缩进推进一级
            visit(child, depth + 1, path + [child_index], render_depth + 1)

    visit(root, 0, root_path)
    if dropped["count"]:
        lines.append(
            "({} node(s) omitted: {} — raise max_tree_nodes to see them)".format(
                dropped["count"],
                "not interactable or not on screen"
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
    prune=True,
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
        prune=prune,
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


def record_still_matches(node, record, window_bounds):
    """按路径解析出来的节点，是否还是快照里那个元素。

    `runtimeId` 是一条子节点下标路径，**位置性**的：树一变，同一条路径就指向
    另一个控件。实测 Nautilus：拿着右键菜单打开时的快照（`9 menu item Rename…`），
    在菜单关掉之后再用 index 9，路径解析到的是工具栏的
    `toggle button Menu (View options)`——于是"重命名"变成了"切换视图选项"，
    而且一路 isError=False，从记录上完全看不出来。

    静默操作错误的控件是最坏的失败模式：不可检测，且可能是破坏性的
    （同一份菜单里紧挨着就是 `Move to Trash`）。宁可解析失败让调用方重新取状态。

    判据按快照里**实际有的**标识逐级收紧，不无中生有：
    - 有 automationId：必须相等（最强，工具包给的稳定 id）
    - 有 name：role + name 必须相等
    - 都没有：role 必须相等，且屏幕位置没变（无名控件只能靠位置指认）
    """
    if node is None:
        return False
    target_id = str(record.get("automationId") or "")
    if target_id:
        return accessible_id(node) == target_id
    target_role = str(record.get("controlType") or "")
    if target_role and node_role(node) != target_role:
        return False
    target_name = str(record.get("name") or "")
    if target_name:
        return node_name(node) == target_name
    if node_name(node):
        # 快照里没名字、现在有名字，说明换了个元素
        return False
    if record.get("frame") is None:
        return True
    return same_frame(record.get("frame"), relative_frame(node, window_bounds))


def find_element(app, record):
    if not record:
        return None
    _, window = main_window(app)
    window_bounds = extents(window)
    node = resolve_path(app, record.get("runtimeId") or [])
    if node is not None and record_still_matches(node, record, window_bounds):
        return node

    target_name = str(record.get("name") or "")
    target_id = str(record.get("automationId") or "")
    target_role = str(record.get("controlType") or "")
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
    # 直接用 CLICK_COVERED_ACTIONS，不再维护一份副本。
    # 两份集合是同一件事的两面（一个决定调用哪个动作、一个决定不要重复展示它），
    # 抄成两处必然分歧：本轮加 `doDefault` 时就漏了这一份，
    # 结果 VS Code 的 19 个节点仍然点不动。
    preferred_exact = CLICK_COVERED_ACTIONS
    if safe(node.get_action_iface) is None:
        return None
    count = int(safe(node.get_n_actions, 0) or 0)
    fallback = None
    for index in range(count):
        name = str(safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(safe(lambda i=index: node.get_action_description(i), "") or "")
        lower = (name or description).lower()
        if lower in preferred_exact:
            return index
        if lower in NON_SELF_ACTIONS:
            continue
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


def active_accessible_window():
    """跨所有应用找出当前上报 ACTIVE 的那个可访问窗口。

    夺焦点失败时用来把"焦点到底在谁身上"讲清楚。找不到**同样是结论**，
    而且是最重要的那个：说明持有输入焦点的东西根本不在无障碍树里。

    实测 VS Code：改完 settings.json 后弹出原生对话框
    「A setting has changed that requires a restart to take effect.」，
    该对话框与 VS Code 同一进程、`_NET_WM_WINDOW_TYPE_DIALOG`、锁住整个应用，
    **但 AT-SPI 里完全不存在**。此时 agent 看到的是一棵正常的树，
    每个动作都被正确拒绝，却无从知道原因——a11y 通道在这里是瞎的。
    """
    # 整段都要能失败得安静。这是**诊断**代码：它的作用是把一条已经确定的
    # 错误讲得更清楚，绝不能反过来把清晰的错误变成一个崩溃堆栈。
    try:
        for app in iter_apps():
            for _, window in app_windows(app):
                if state_contains(window, Atspi.StateType.ACTIVE):
                    return node_name(app), node_name(window)
    except Exception:
        return None, None
    return None, None


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
    app_name, window_name = active_accessible_window()
    if app_name is None:
        culprit = (
            " No window in the accessibility tree currently reports ACTIVE, so "
            "input focus is held by something the tree cannot see — most often a "
            "native dialog (Electron and some GTK apps do not expose theirs). "
            "Call get_screenshot to find out what is on top and dismiss it."
        )
    else:
        culprit = " Input focus is currently held by '{}' in '{}'.".format(
            window_name or "(untitled window)", app_name
        )
    raise RuntimeError(
        "Refusing to synthesize {}: could not bring the target window to the "
        "foreground. Input synthesis is global and would be delivered to "
        "whichever window currently holds focus.{}".format(what, culprit)
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


# 应用解析的重试次数与间隔。见 resolve_app() 的说明：应用忙于重建窗口时，
# 枚举可能瞬时读不到，而"读不到"被当成"不存在"会让 agent 走上完全错误的分支。
RESOLVE_APP_ATTEMPTS = int(os.environ.get("OPEN_COMPUTER_USE_RESOLVE_ATTEMPTS", "3"))
RESOLVE_APP_RETRY_SECONDS = float(
    os.environ.get("OPEN_COMPUTER_USE_RESOLVE_RETRY", "0.3")
)

# 调完开菜单的动作后等多久再判断菜单有没有真的弹出来。菜单是异步弹的，
# 立刻去查必然查不到，会把能用的语义动作误判成失效并多合成一次右键。
MENU_SETTLE_SECONDS = float(os.environ.get("OPEN_COMPUTER_USE_MENU_SETTLE", "0.6"))

# 语义上等价于"打开右键菜单"的动作名。只有这一类动作才允许在语义调用无效时
# 自动回落到合成右键：开菜单是幂等的，重复一次没有副作用。其它二级动作
# （delete / cut / send-to-trash…）绝不能重试——动作可能已经生效只是观测不到，
# 再来一次就是执行了两次。
CONTEXT_MENU_ACTIONS = {
    "menu",
    "show menu",
    "context menu",
    "popup",
    "show context menu",
    "secondary",
}


def context_menu_visible(app):
    """应用当前是否弹出了菜单。

    两种形态都要认：
    - X11 上 GTK/Qt 的右键菜单通常是**独立顶层窗口**（Nautilus 实测如此，
      角色 `window`、无名字）；
    - 也有实现把它挂成主窗口内的 popover/popup menu。

    只查顶层窗口及其浅层子节点，代价固定，可以放在动作路径上。
    """
    if app is None:
        return False
    for index in range(min(child_count(app), MAX_CHILD_FANOUT)):
        top = child_at(app, index)
        if top is None or not state_contains(top, Atspi.StateType.SHOWING):
            continue
        if node_role(top) in MENU_ROLES:
            return True
        for depth_one in range(min(child_count(top), 32)):
            near = child_at(top, depth_one)
            if near is None:
                continue
            if node_role(near) in MENU_ROLES and state_contains(
                near, Atspi.StateType.SHOWING
            ):
                return True
    return False


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

# 语义调用同样不能当成"生效"的证据。这不是保守措辞，是实测结论：
#   Nautilus  文件图标的 `menu`   -> do_action True，菜单一个都不弹（焦点/选中三种前置都试过）
#   GIMP      图层 cell 的 `activate` -> do_action True，活动图层不变（截图 0 像素差异）
#   VLC       单选按钮的 `Toggle`  -> CHECKED 真的翻转了，面板却不切换
# 最后一条尤其要紧：**判据不能读被操作节点自身的状态**，状态会跟着变、行为没有。
#
# do_action 的返回值只说明工具包接受了这次调用，不说明界面发生了任何事。
# 把它当成成功上报，等于向 agent 谎报事实——它会据此推进下一步，
# 而真实界面还停在原地。
UNVERIFIED_SEMANTIC = (
    "The toolkit accepted the call; that is not evidence the action took effect. "
    "AT-SPI actions on Linux routinely report success while doing nothing. "
    "Confirm from the returned state before treating this as done."
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


def is_dropdown_item(node, window):
    """这个元素是不是"下拉列表里的一项"。

    这类元素上的语义调用有一个特殊的坏处：**它会关掉弹窗，但不提交值**。
    实测 LibreOffice 7.3「格式 → 段落 → 行距」下拉，点 `table cell Double`：

        do_action  -> 返回 True，下拉关闭，控件仍然显示 Single
        坐标点击   -> 下拉关闭，控件显示 Double            （截图核实）

    比"不生效"更糟的是**事后无法校验**：弹窗连同那个元素一起消失了，
    动作前后的树必然不同，通用的"什么都没变就重试"判据在这里不会触发；
    而 LibreOffice 对话框里的 combo box 是个不上报值的幻影节点
    （没有 frame、text 为空），想读回来确认也读不到。
    所以只能在**调用之前**就避开语义通道。

    影响面不小：调研 OSWorld 官方 370 个任务后，下拉/调色板提交横跨
    Calc / Impress / Writer 三个应用 ≥37 个任务，是 LibreOffice 侧的头号阻塞。

    判据刻意收得很窄——弹出窗口里的 `table cell`：
    - 菜单项不在此列。Nautilus 右键菜单的 `menu item Rename…` 实测
      do_action 完全正常，一并改掉会把好路也堵死。
    - 主窗口里的表格单元格不在此列。Calc 的单元格用的就是这个角色，
      它们不是下拉项，语义调用没有这个问题。
    """
    if node is None or window is None:
        return False
    if node_role(node) != "table cell":
        return False
    # 下拉在 X11 上是独立顶层窗口，角色 `window` 且没有标题；
    # 应用主窗口是 `frame`，对话框是 `dialog`/`alert`，都带名字。
    return node_role(window) == "window" and not node_name(window)


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
            prune=operation.get("prune", True),
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
            notes.append(
                SEMANTIC
                + "Invoked the element's AT-SPI accessibility action. "
                + UNVERIFIED_SEMANTIC
            )
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
                    x,
                    y,
                    UNVERIFIED_SYNTHESIS,
                    # 调用方已经指定了元素，再劝它"改用 element_index"是答非所问：
                    # 走到这里通常正是因为语义调用失效了（实测多个应用的
                    # AT-SPI 动作返回成功却不生效），此时坐标是唯一出路。
                    "" if element_record else PREFER_ELEMENT_INDEX,
                )
            )
        elif click_method == "auto":
            handled = False
            if (
                element is not None
                and operation.get("mouse_button", "left") == "left"
                and not is_dropdown_item(element, window)
            ):
                handled = do_action_by_index(element, preferred_action_index(element))
            if handled:
                notes.append(
                SEMANTIC
                + "Invoked the element's AT-SPI accessibility action. "
                + UNVERIFIED_SEMANTIC
            )
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
                if is_dropdown_item(element, window):
                    # 说清楚为什么没走语义通道。不解释的话这条 Note 读起来像
                    # "这个元素没有动作"，与事实相反——它有，只是那个动作会
                    # 关掉下拉却不提交值，而且关掉之后连校验的机会都没有。
                    reason = (
                        "This element is an item inside a drop-down popup, where the "
                        "AT-SPI action closes the popup without committing the value "
                        "and leaves nothing to verify against, so this went straight to "
                        "a coordinate click at ({:.0f}, {:.0f}). ".format(x, y)
                    )
                else:
                    reason = (
                        "No usable AT-SPI action was available, so this fell back to a "
                        "coordinate click at ({:.0f}, {:.0f}) after bringing the window "
                        "to the foreground. ".format(x, y)
                    )
                notes.append(SYNTHESIS + reason + UNVERIFIED_SYNTHESIS)
        else:
            raise RuntimeError("Invalid click_method '{}'".format(click_method))
    elif tool == "perform_secondary_action":
        action = operation.get("action", "")
        # 只有"开右键菜单"这类幂等动作才做校验+回落。做不到这一点的话，
        # 一个已经生效但观测不到的破坏性动作会被重复执行。
        opens_menu = str(action).lower() in CONTEXT_MENU_ACTIONS
        had_menu = context_menu_visible(app) if opens_menu else False
        invoke_secondary_action(element, action)
        notes.append(
            SEMANTIC
            + "Invoked the '{}' AT-SPI action. ".format(action)
            + UNVERIFIED_SEMANTIC
        )
        if opens_menu and not had_menu:
            time.sleep(MENU_SETTLE_SECONDS)
            if not context_menu_visible(app):
                # 实测 Nautilus：文件图标的 `menu` 动作**永远返回成功、永远不开
                # 菜单**，与是否聚焦/选中无关。语义通道在这里是死路，只能合成。
                x, y = screen_point(bounds, element_record, None, None)
                require_window_focus(window, "perform_secondary_action")
                send_mouse_click(x, y, "right", 1)
                notes.append(
                    SYNTHESIS
                    + "The '{}' action reported success but no menu appeared, so this "
                    "fell back to a synthesized right-click at ({:.0f}, {:.0f}). "
                    "{}".format(action, x, y, UNVERIFIED_SYNTHESIS)
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
                + "Wrote the text through the AT-SPI editable-text API and read it back "
                "to confirm it landed ({} -> {} characters). This confirms the control "
                "changed; if the control belongs to a dialog, the application may still "
                "require OK/Apply before the value takes effect.".format(
                    before_chars, after_chars
                )
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
        notes.append(
            SEMANTIC
            + "Wrote the value through the AT-SPI API and read it back to confirm the "
            "control now holds it. Note the limit of that check: it confirms the "
            "CONTROL changed, not that the application adopted the value. Dialogs "
            "commonly keep control state separate from document state and only commit "
            "on OK/Apply — verify the actual effect (document content, a re-read of "
            "the relevant element) rather than trusting this line."
        )
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
