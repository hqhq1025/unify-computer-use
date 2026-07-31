#!/usr/bin/env python3

import base64
import hashlib
import json
import math
import os
import re
import subprocess
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
# 动作之后、建快照之前的最短安置时间。顶层窗口集合没变时就只等这么久，
# 与加入 settle 判据之前的行为完全一致。
SETTLE_MIN_SECONDS = 0.12
# 开出/关掉窗口之后，最多再等多久让焦点落定。实测焦点转移只要 0.053s，
# 这里留了十几倍余量；超时说明新窗口压根没接管焦点（比如提示气泡这类
# 不可聚焦的顶层），带着当前状态返回并**明说没稳定**，好过把工具卡死。
SETTLE_FOCUS_TIMEOUT_SECONDS = 0.8
SETTLE_POLL_SECONDS = 0.02
# 这些工具的截图**不可关**，`#29` 的 A/B 也不例外。
#
# `drag` 是目前唯一一个：它两头都够不着 a11y。
# - **执行**：三处实现（官方 Codex macOS、本仓库 macOS Kit、本仓库 Linux）
#   都只接受 `from_x/from_y/to_x/to_y`，没有 `element_index`。这不是遗漏——
#   拖拽的**目标位置通常不是元素**（"移到幻灯片下方 15cm 处"不是一个元素），
#   所以元素锚定从根上解决不了它。
# - **验证**：实测把 Impress 的标题从 0.76cm 拖到 15.00cm 之后，
#   a11y 树里该元素的 `Frame` **一点没变**，只有状态栏文本变了。
#   树接不住拖拽的效果，所以事后必须有一张图。
SCREENSHOT_REQUIRED_TOOLS = {"drag_xy", "click_xy"}


def a11y_screenshots_enabled():
    """a11y 轨要不要顺带带截图。默认**开**。

    设 `OPEN_COMPUTER_USE_A11Y_SCREENSHOTS=0` 关掉——这是 `#29`（截图 A/B）
    的开关，而不是给日常使用准备的。关掉之后
    `SCREENSHOT_REQUIRED_TOOLS` 里的工具仍然带图。
    """
    value = os.environ.get("OPEN_COMPUTER_USE_A11Y_SCREENSHOTS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
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
    # Gecko/Thunderbird 用**结果状态**给复选框的动作命名，而不是 "toggle"：
    # 勾上时提供 `uncheck`、没勾时提供 `check`，任一时刻只暴露适用的那一个。
    # 因此调用它就等于 toggle，正是 click 该做的事。
    # 不认这两个名字的话，Gecko 的复选框全部退回坐标点击——而设置类界面
    # 几乎全是复选框（Thunderbird 5/14 个任务是账户设置、3/14 是消息过滤器）。
    "check",
    "uncheck",
}

# 名字里含 click/press/activate，但**作用对象不是这个元素本身**的动作。
# 必须显式排除：`preferred_action_index()` 的兜底是子串匹配，
# `clickAncestor` 会被它匹中，于是 agent 以为点中了目标，
# 实际点的是祖先节点——而且从返回值和树里都看不出来。
# VS Code 实测有 14 个节点的动作表是 ('clickAncestor', 'showContextMenu')。
NON_SELF_ACTIONS = {"clickancestor"}


def normalized_action(label):
    """把动作名归一到只剩字母数字，再做比对。

    同一个语义在不同工具包里拼法不同：Chromium/Electron 叫 `clickAncestor`，
    Gecko/Thunderbird 叫 `click ancestor`（带空格）。只排掉其中一种，
    另一种照样会被子串兜底匹中，于是 agent 又去点了祖先节点。
    实测两者都出现过——窄判据漏变体，这里按归一化比。
    """
    return "".join(ch for ch in str(label or "").lower() if ch.isalnum())

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


def window_identity_set(app):
    """当前顶层窗口的身份集合，用来判断一次动作有没有开出或关掉窗口。

    身份取 (索引, 角色, 标题)。刚映射出来的窗口可能先是空标题、稍后才拿到
    真正的标题，这会让同一个窗口在集合里换一个身份——**这不影响判据**：
    它照样落在"新出现"那一侧，而这里要的正是"有东西变了"这个事实。
    """
    identity = set()
    for index, window in app_windows(app):
        identity.add((index, node_role(window), node_name(window)))
    return identity


def wait_for_ui_to_settle(
    app,
    before,
    min_seconds=SETTLE_MIN_SECONDS,
    timeout_seconds=SETTLE_FOCUS_TIMEOUT_SECONDS,
    poll_seconds=SETTLE_POLL_SECONDS,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """动作之后等界面安置好，再让调用方去建快照。

    修的是一个**静默操作错误窗口**的竞态。实测（Thunderbird
    `Tools → Message Filters`，AT-SPI 2.44 + GNOME Shell）：

        t=0.070s  新窗口已经进入 AT-SPI 树，但 ACTIVE 还挂在主窗口身上
        t=0.123s  ACTIVE 才转移过去（X11 的 _NET_ACTIVE_WINDOW 同一刻翻转）

    而这里原先是固定 `sleep(0.12)`——**正好压在边界上**。快照因此有相当概率
    落在 0.070~0.123 这段窗口期里建成：树是完整的、状态是自洽的、
    `main_window()` 也按既有判据挑得没错，挑出来的却是主窗口。
    agent 拿着主窗口的树按索引点击，就静默地操作了另一个窗口——
    实测后果是在 Message Filters 里点 `New…`，弹出的是主窗口的「新建邮件」。

    **把固定 sleep 调大修不了它**：那只是把边界挪到另一个不确定的位置，
    应用越慢、机器越忙越容易重新撞上。判据必须从"等够时间"换成"等到状态"。

    完成条件分两种：
    - 顶层窗口集合与动作前一致 → 什么都没开也没关，立刻返回（常见路径不变慢）。
    - 集合变了 → 等到**新出现的那个窗口**报 ACTIVE 或 MODAL 为止；
      如果只有窗口关闭，则等到剩下的窗口里有人接管焦点。

    超时**不静默**：返回一条说明，因为"新窗口没接管焦点"意味着这份快照可能
    照的不是 agent 以为的那个窗口，这正是它需要知道的事。

    **边界：窗口在最短安置时间之后才出现的，这里接不住。**
    实测六例开窗口，进入 AT-SPI 树的耗时都 ≤ 0.070s：

        Thunderbird 消息过滤器 (Gecko) 0.070   LibreOffice 查找替换 (VCL) 0.045
        gedit 查找替换       (GTK)   0.045   gedit 另存为        (GTK) 0.064
        VLC 首选项           (Qt)    0.046

    `SETTLE_MIN_SECONDS` = 0.12 对最慢的一例仍有 1.7 倍余量，所以没有再加一段
    固定观察期——那要给**每一个**动作加上几百毫秒，用真实延迟去买一个没量到的
    余量。更慢的应用漏掉之后是另一种失败：快照照到的是动作前的状态，
    新窗口完全不出现。那是**看得见**的失败（agent 会发现动作像是没生效），
    与这里要修的"树自洽、却照错了窗口"不是一回事，不要混为一谈。
    """
    sleep(min_seconds)
    if before is None:
        return None
    deadline = clock() + timeout_seconds
    while True:
        try:
            windows = app_windows(app)
            current = {
                (index, node_role(window), node_name(window))
                for index, window in windows
            }
        except Exception:
            # 应用可能被这次动作关掉了。安置等待失败不该盖住真正的错误——
            # 紧接着的 build_snapshot 会把它报出来。
            return None
        if current == before:
            return None
        appeared = current - before
        for index, window in windows:
            focused = state_contains(window, Atspi.StateType.ACTIVE) or state_contains(
                window, Atspi.StateType.MODAL
            )
            if not focused:
                continue
            if not appeared or (index, node_role(window), node_name(window)) in appeared:
                return None
        if clock() >= deadline:
            return (
                "The set of top-level windows changed but no window took focus within "
                "{:.1f}s. The snapshot below may show a different window than the one "
                "this action opened — confirm the window title before using any "
                "element_index from it.".format(timeout_seconds)
            )
        sleep(poll_seconds)


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
            normalized_action(lower) not in NON_SELF_ACTIONS
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
    进而在 click(element_index) 与 invoke_element_action 之间反复摇摆，
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


# 一个下拉框最多往下翻这么多节点去找"当前选中的是哪一项"。
# 选项列表通常几项到几十项，200 足够，而封顶保证这条路径的代价是有界的。
SELECTION_SCAN_BUDGET = 200


def selected_option_name(node):
    """下拉框当前选中的是哪一项。

    这补的是一个**大缺口**：此前 agent 在树里根本看不见任何下拉框的值。
    实测 Chrome 的打印对话框——`combo box "Margins"`、`"Destination"`、
    `"Scale"` 三个，树里全都只有名字、没有值，而任务恰恰要求"把边距设成 None"。
    看不见当前值，就既没法判断要不要改，也没法确认改成功了。

    值不在 Value 接口上（Chrome 的 combo box 根本不实现 Value），也不在 Text
    接口上（实测读回来是一个对象替换符 `￼`）。它在**后代 menu item 的
    SELECTED 状态**上——而且**下拉关着时也读得到**，这一点是实测确认的，
    否则这个功能就得先展开每一个下拉框，代价完全不可接受。

    有些下拉框确实读不到（实测 `combo box "Layout"` 就是 None）。那种情况下
    返回空串、树里不显示值——**不猜**。少给一个信号，好过给一个编出来的。
    """
    if node is None:
        return ""
    stack = [node]
    seen = 0
    while stack and seen < SELECTION_SCAN_BUDGET:
        current = stack.pop()
        seen += 1
        if current is not node and state_contains(current, Atspi.StateType.SELECTED):
            name = str(node_name(current) or "").strip()
            if name:
                return name
        for index in range(min(child_count(current), 100)):
            child = safe(lambda: current.get_child_at_index(index))
            if child is not None:
                stack.append(child)
    return ""


def element_value(node, text_limit=DEFAULT_TEXT_LIMIT):
    direct = text_value(node, text_limit=text_limit) or numeric_value(node)
    if direct:
        return direct
    # 只对下拉类角色做这次扫描，别的角色不付这个代价。
    role = str(node_role(node) or "")
    if role in ("combo box", "list box"):
        return limit_text(selected_option_name(node), text_limit=text_limit)
    return direct


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


# 动作名 -> 它承诺会改变的东西。**表里的名字全部来自实测**，不是猜的。
#
# 思路照抄 Playwright 的 `_setChecked`（packages/playwright-core/src/server/dom.ts）：
#
#     const finalState = await isChecked(progress);
#     if (finalState.matches !== state)
#       throw new NonRecoverableDOMError('Clicking the checkbox did not change its state');
#
# 它抛的是 **NonRecoverableDOMError**——不重试，直接抛。重复同一个动作不会有
# 不同结果。
#
# **第一版这张表是照 Playwright 的语义写的**（check / uncheck / expand / collapse
# / select / deselect），实测六个应用**一个都没有**，整张表是死代码。
# 真实存在的是下面这些（gedit / Nautilus / VLC / VS Code / GIMP / Thunderbird
# 全量扫出来的动作名）：
#
#     click 1197 / press 144 / activate 138 / expand or contract 119 / edit 119
#     menu 81 / open 80 / click ancestor 75 / showcontextmenu 61 / release 33
#     showmenu 27 / dodefault 18 / setfocus 4 / increase 3 / decrease 3 / toggle 2
#
# 注意 `expand or contract` 和 `toggle` 是**翻转**语义，不是"设成某个值"——
# 所以判据是"必须翻转"，不是"必须等于某值"。
ACTION_MUST_FLIP = {
    "expand or contract": "EXPANDED",
    "expandorcontract": "EXPANDED",
    "toggle": "CHECKED",
}
# 方向明确的动作：值必须变。
ACTION_MUST_CHANGE_VALUE = ("increase", "decrease")


def readable_states(node):
    """读回那几个**可判定**的状态；读不到就返回 None（不返回空字典冒充读到了）。"""
    if node is None:
        return None
    out = {}
    for name, _label in NOTABLE_STATES:
        state = getattr(Atspi.StateType, name, None)
        if state is None:
            continue
        out[name] = state_contains(node, state)
    return out or None


def state_transition_note(action, before, after):
    """动作承诺会改变什么时，动作后回读校验。

    返回 (note, failed)。failed=True 表示这是**不该重试**的失败——
    重复同一个动作不会有不同结果。

    **边界必须写清楚：状态翻转证明的是"控件状态动了"，不是"行为发生了"。**
    本仓库实测过反例：VLC 首选项里那颗单选按钮，`Toggle` 之后 CHECKED 真的
    翻转了，面板却**不切换**。所以这条判据接不住"状态变了行为没变"那一类。
    把它当万能验证会制造假阳性——它只接住一类：**动作承诺翻转，而状态没翻**。
    """
    name = ACTION_MUST_FLIP.get(str(action).strip().lower())
    if name is None or not before or not after:
        return None, False
    was, now = before.get(name), after.get(name)
    if was is None or now is None:
        return None, False
    if was != now:
        return ("[state] {} flipped {} -> {}, which is what the '{}' action promises. "
                "Note the limit: this proves the CONTROL changed, not that the "
                "application acted on it — measured on VLC, a radio button's CHECKED "
                "flipped while the panel it selects did not switch. Confirm the actual "
                "effect before treating this as done."
                .format(name, was, now, action), False)
    return ("The '{}' action reported success, but {} is still {} — that action promises "
            "to flip it, so nothing happened. Repeating the same call will not help; "
            "the toolkit accepted it without acting. Reach the element with a coordinate "
            "click instead (click_xy, or click with click_method \"global\")."
            .format(action, name, now), True)


def plain_text_from_rich_text(value):
    """把 Qt 的富文本 tooltip 还原成纯文本。

    Qt 会把 tooltip 存成一整段 HTML，带 `<head>` 里的 CSS。VLC 首选项实测：
    19 段这样的 HTML 合计 9149 字符，占整次观测的 **56%**，而其中真正的信息
    往往只有一句话。不处理的话，`Description:` 段会把整个观测预算吃掉。

    判据卡在 `<html>` 开头——这是 Qt 富文本的标志。**不对普通文本做剥离**：
    真实内容里完全可能出现尖括号（代码、模板、数学表达式），
    对它们动手会篡改 agent 读到的数据。
    """
    text = str(value or "")
    if not text.lstrip().lower().startswith("<html"):
        return text
    # `<head>` 里全是 CSS，剥完标签会漏成 `p, li { white-space: pre-wrap; }`，
    # 所以整块丢掉而不是逐个剥标签。
    text = re.sub(r"(?is)<head>.*?</head>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return " ".join(text.split())


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
    raw = str(safe(node.get_description, "") or "")
    return limit_text(plain_text_from_rich_text(raw), text_limit=text_limit)


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


def quoted(text):
    """把自由文本包成带转义的引号。

    树里的名字**可以包含冒号**——实测 LibreOffice Impress 就有
    `panel PageShape: Weekday in school`。原来的格式是
    `<idx> <role> <name> Description: <desc>`，于是名字里的冒号与
    `Description:` 这个分隔符在词法上**无法区分**：agent 想从一行里切出
    "名字到底是什么"，没有任何可靠办法。我们发出去的是一种歧义文法。

    Playwright 的 aria snapshot 用引号定界正是为了这个（`- role "name"`）。
    """
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r", "\\r").replace("\n", "\\n")
    return '"' + escaped + '"'


def render_element_line(record, render_depth, boxes=True):
    """把一条元素记录渲染成一行。文法（借鉴 Playwright 的 aria snapshot）：

        <缩进><index> <role> "<name>" [<states>] [desc="…"] [placeholder="…"]
                     [actions=a,b] {x,y,w,h}: "<value>"

    - 所有自由文本一律**加引号**，消掉旧格式里名字与字段分隔符撞车的歧义
    - 附加属性一律进**方括号**，与 aria snapshot 的 `[checked]` `[level=1]` 同构
    - 几何压成 `{x,y,w,h}`：旧写法 `Frame: {x: 687, y: 23, width: 64, height: 46}`
      是 45 字符，新写法 14 字符，**每行省 31 字符而信息不少一个字**。
      实测几何占整棵树的 35–50%，这一项就能砍掉其中约三分之二。
    - `boxes=False` 时整段几何都不渲染（对齐 Playwright 的 `boxes` 开关）。
      **这不削弱任何能力**：`click(element_index)` 的坐标是服务端内部从记录里
      取的，不经过这一行；而 agent 需要坐标时截图恒带、且与树同一个坐标空间
      （已实测：真实 agent 只靠截图命中了 a11y 树里零存在的 GIMP 画布目标）。
    - 值放在**最后、冒号之后**，对齐 aria 的 `- textbox: Enter your name`
    - 空的段一律省略，不留空壳
    - `index` 保留在行首：我们没有 selector，它是唯一的引用手段
    """
    role = record["localizedControlType"] or record["controlType"] or "element"
    title = record["name"] or record["automationId"] or ""
    parts = ["{} {}".format(record["index"], role)]
    if title:
        parts.append(quoted(title))
    state_seg = record.get("states", "")
    if state_seg:
        parts.append(state_seg.strip())
    if record.get("description") and record["description"] != title:
        # 单独一段，不并进 name：name 是元素身份的一部分（轨迹按 role+name
        # 匹配）。GTK 把可读标签放在这里的情况很常见，尤其是纯图标按钮。
        parts.append("[desc=" + quoted(record["description"]) + "]")
    if record.get("placeholder") and record["placeholder"] != title:
        # 提示不是内容——控件其实是空的，所以绝不能混进值里。
        parts.append("[placeholder=" + quoted(record["placeholder"]) + "]")
    if record["actions"]:
        parts.append("[actions=" + ",".join(record["actions"]) + "]")
    if boxes and record["frame"] is not None:
        f = record["frame"]
        parts.append("{{{0},{1},{2},{3}}}".format(
            round(f["x"]), round(f["y"]), round(f["width"]), round(f["height"])))
    line = ("\t" * (render_depth + 1)) + " ".join(parts)
    if record["value"] and record["value"] != title:
        line += ": " + quoted(record["value"])
    return line


def state_segment(node, has_click_action=False, borrowed_name=False):
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
    # 名字是从 LABELLED_BY 借来的就要标出来，理由不是洁癖：同一个「位置和大小」
    # 对话框里 `Position Y` 出现了**两次**（两个标签页各一个）。调用方需要知道
    # 这个名字是借来的、因此可能不唯一——选择器命中多个时会报歧义，
    # 那时这个标记就是解释。
    if borrowed_name:
        marks.append("labelled")
    if not marks:
        return ""
    return " [" + " ".join(marks) + "]"


class StableIndexer:
    """给元素发编号，让编号**跨快照存活**。

    照抄 Playwright 的 `ariaSnapshot.ts`：

        let ariaRef = element._ariaRef;
        if (!ariaRef || ariaRef.role !== role || ariaRef.name !== name) {
          ariaRef = { role, name, ref: 'e' + (++lastRef) };
          element._ariaRef = ariaRef;
        }

    **ref 不是遍历序号，是挂在元素身上、以 (role, name) 为失效条件的缓存 id。**
    同一个元素只要 role 和 name 都没变，跨多少次快照都是同一个号；
    role 或 name 变了就重新发号（因为它已经不是"同一个东西"了）。

    我们没法往 AT-SPI 对象上挂属性（每次操作都是新进程），但 Go 侧本来就缓存着
    上一份快照，把 `路径 -> (编号, role, name)` 传进来就等价了。

    修的是实测踩过的最贵的一类失败：F4 打开对话框后索引全部重排，用旧下标调
    click 时工具照点不误——本想点 Position Y，实际点到菜单，把对象高度误改成
    16.26cm，**全程零报错**。有了稳定编号，对话框关掉之后主窗口的控件会**拿回
    原来的号**，旧引用要么仍然正确、要么明确报"不存在"。
    """

    def __init__(self, known):
        # known: {"0.1.2": {"index": 7, "role": "push button", "name": "Save"}}
        self.known = known or {}
        self.next_free = 0
        for entry in self.known.values():
            try:
                self.next_free = max(self.next_free, int(entry.get("index", 0)) + 1)
            except (TypeError, ValueError):
                pass
        self.used = set()

    def index_for(self, path, role, name):
        key = ".".join(str(p) for p in path)
        entry = self.known.get(key)
        if entry is not None and entry.get("role") == role and entry.get("name") == name:
            index = entry.get("index")
            if isinstance(index, int) and index not in self.used:
                self.used.add(index)
                return index
        while self.next_free in self.used:
            self.next_free += 1
        index = self.next_free
        self.used.add(index)
        self.next_free += 1
        return index


def labelled_by_name(node):
    """无名控件从 AT-SPI 的 `LABELLED_BY` 关系上借一个名字。

    这条一等信息源本仓库此前**从未读过**（全仓 grep get_relation_set /
    RelationType / LABELLED 零命中）。它治的正是我们最疼的病：手工跑 Impress
    时，「位置和大小」对话框里的 spin button 全部无名，只能靠数顺序点，
    那是最脆的一种定位。

    实测判据（LibreOffice 7.3「位置和大小」）：**13 个无名 spin button，
    13 个都拿到了名字**，包括 `Position Y:` / `Position X:` / `Width:` /
    `Height:` / `Angle:` / `Radius:`。定位方式因此从"数第几个"变成"按名字选"。

    **裁剪判据里刻意不查这个关系。** 那道判据跑在全部节点上（GIMP 一次
    render_tree 调用它 3162 次），把 DBus 往返放进去会原样撞回它当初要解决的
    那个问题——完整 record_for 曾让 GIMP 整棵树要 38s、超过 Go 层 30s 的超时。
    代价是：一个**仅仅因为借到名字才值得保留**的节点会被裁掉。实测未发生
    （「位置和大小」的 spin button 因为带动作而保留），但这是已知的边界。

    只对**自身无名**的节点查，原因同样是成本：get_relation_set 是每节点一次
    DBus 往返，实测 1.037ms/节点。全量查的话 GIMP 的 3261 个节点要多花
    3.4 秒，而它的 get_app_state 已经在 30s 超时线上。而增益**全部**落在
    无名节点上——一个已经叫 `Save` 的按钮再关联一个 `Save` 标签，
    对 agent 没有任何新信息。

    这不是"编一个名字"。无障碍名字的标准算法本来就包含 labelled-by 这一路
    （Playwright 的 accessible name 同样如此）；是工具包选择了把名字放在
    关系里而不是放在节点上。但来源仍然要能看见，所以渲染时会带 `labelled`
    标记——同一个对话框里 `Position Y:` 出现了两次（两个标签页各一个），
    调用方需要知道这个名字是借来的、可能不唯一。
    """
    relations = safe(lambda: node.get_relation_set(), None)
    if not relations:
        return ""
    names = []
    for relation in relations:
        kind = safe(lambda: relation.get_relation_type())
        if kind != Atspi.RelationType.LABELLED_BY:
            continue
        count = safe(lambda: relation.get_n_targets(), 0) or 0
        for index in range(count):
            target = safe(lambda: relation.get_target(index))
            if target is None:
                continue
            text = str(node_name(target) or "").strip()
            if text:
                names.append(text)
    # 标签文字常带尾冒号（`Position Y:`），去掉——选择器里写冒号很别扭，
    # 而冒号不携带任何信息。
    return " ".join(names).rstrip(":").strip()


def effective_name(node):
    """一个节点**对外的名字**——自身的，没有就从 LABELLED_BY 借。

    这必须是**唯一**的口径，凡是要比对名字的地方都从这里取。

    上一次没做到这件事的代价记在 commit 5543a52：record 里存的是截断过的
    名字，而重解析时拿完整名字去比，必然失配；失配是静默的，身份判据悄悄退到
    最弱的"role + 屏幕位置"，元素一动就指向别人。

    借名字会**原样重现**那个坑：记录里写着 `Position Y`，而 node_name() 在
    同一个节点上返回空串。所以 record_for 与 record_name_matches、
    find_element 的兜底扫描全部改走这里，一处口径，两边同尺。
    """
    own = node_name(node)
    if str(own or "").strip():
        return own
    return labelled_by_name(node)


def record_for(node, index, path, window_bounds, text_limit=DEFAULT_TEXT_LIMIT):
    bounds = relative_frame(node, window_bounds)
    role = node_role(node)
    # 动作表只问一次，两个字段共用——见 node_actions() 里关于 LibreOffice
    # ATK 桥的说明。
    actions, has_click_action = node_actions(node)
    own_name = node_name(node)
    full_name = effective_name(node)
    borrowed = bool(str(full_name or "").strip()) and not str(own_name or "").strip()
    name = limit_text(full_name, text_limit=text_limit)
    record = {
        "index": index,
        "runtimeId": path[:],
        "automationId": accessible_id(node),
        "name": name,
        "controlType": role,
        "localizedControlType": role,
        "className": str(safe(node.get_toolkit_name, "") or ""),
        "value": element_value(node, text_limit=text_limit),
        "nativeWindowHandle": 0,
        "frame": bounds,
        "actions": actions,
        "states": state_segment(
            node, has_click_action=has_click_action, borrowed_name=borrowed
        ),
        "description": node_description(node, text_limit=text_limit),
        "placeholder": placeholder_text(node, text_limit=text_limit),
        # 格式属性跟着记录走，**不单独遍历**。树渲染本来就在访问每个节点，
        # 而 Go 侧已经缓存着动作前的快照，两份一比就是零额外 AT-SPI 调用。
        #
        # 走过一版弯路：先按"焦点文本节点"取，结果 LibreOffice 的 root pane
        # **自带 FOCUSED 位**（这个坑仓库里早记过），取到的永远是它；
        # 改成扫全树又要 551ms 且漏掉目标段落。跟着 record 走两个问题都没有。
        "textAttributes": text_attributes(node),
    }
    # 名字被截断时，另外记一份**完整名字的哈希**。
    #
    # 不记的代价是实测过的：record_still_matches 拿实时的完整 name 去比
    # record 里已截断的 name，必然失配。同一个节点、同一个名字，只要
    # text_limit 从 500 调到 40，身份判定就从 True 翻成 False，
    # 然后静默退到最弱的"role + 屏幕位置"判据。
    # 也就是说：**agent 为省 token 调低 text_limit，会悄悄削弱元素身份**。
    #
    # 存哈希而不是存阈值，是写测试时改的：只按截断后的前缀比，两个前缀相同的
    # 不同元素会被判成同一个——那等于把一个静默失配换成一个静默误判，更糟。
    # 哈希让判定回到"完整名字精确相等"，代价是极少数记录上多 16 个字符。
    if len(full_name) > len(name):
        record["nameHash"] = name_fingerprint(full_name)
    return record


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
            # 单元格用 (行, 列) 当路径后缀——它比遍历序号稳，滚动之后同一个格子
            # 还是同一个号。
            cell_path = path + [row, col]
            index = (indexer.index_for(cell_path, node_role(cell), "")
                     if indexer is not None else len(records))
            record = record_for(
                cell, index, cell_path, window_bounds, text_limit=text_limit
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
                boxes=True, indexer=None,
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

        # 裁剪：只保留"可操作角色 + 屏幕上可见"的节点。与 OSWorld 官方判据同源，
        # 实测 22% 压缩率、100% 保留率。被裁的只是它自己这一行，**仍然继续递归
        # 子节点**——中间容器往往正是有价值控件的父节点，连子树一起砍会适得其反。
        #
        # 判据**先只读它真正用到的四个字段**，不要先建整条记录。
        # 实测 GIMP（GAIL）：完整的 record_for 是 8.45ms/节点，而一次 render_tree
        # 调用它 3162 次却只产出 157 条记录——**95% 的开销当场丢弃**，
        # 占渲染耗时的 87%，整棵树 38s，超过 Go 层 30s 的超时。
        # 也就是说 a11y 通道在 GIMP 上**默认根本用不了**，而原因只是取数顺序。
        #
        # 这四个字段合计约 2.3ms；状态段(2.52)、动作表(1.78)、值(0.92)、
        # 占位符(0.58)、automationId(0.57) 只在节点确定保留时才读。
        # 幸存者会把这四个字段重读一遍，但幸存率只有 5%，重读的代价可以忽略。
        if prune and depth > 0:
            probe_frame = relative_frame(node, window_bounds)
            probe_role = node_role(node)
            # 有名字的可见节点一律保留，哪怕角色不"可交互"。
            # 实测教训：行距 combo 的 toggle button 本身没有名字，agent 只能靠
            # 父节点 `panel Line Spacing` 指认它。纯按角色白名单裁掉这个 panel，
            # 目标元素虽然还在树里，却**没法被指认**——整条对话框链路当场断掉。
            # 保留率指标只看"目标在不在"，看不到这一层，是它的盲区。
            keeps = probe_frame is not None and (
                is_interactive_role(probe_role)
                or bool(limit_text(node_name(node), text_limit=text_limit))
                or bool(node_description(node, text_limit=text_limit))
            )
            if not keeps:
                dropped["count"] += 1
                for child_index in range(min(child_count(node), MAX_CHILD_FANOUT)):
                    # 本节点没渲染，render_depth 不推进，子节点顶替它的位置
                    visit(child_at(node, child_index), depth + 1,
                          path + [child_index], render_depth)
                return

        record = record_for(node, None, path, window_bounds, text_limit=text_limit)

        # 预算吃紧时优先保住有名字/有动作/有值的节点。丢容器只丢它自己这一行，
        # 仍然继续递归子节点——被丢的容器往往正是有价值控件的父节点。
        if len(records) >= pressure_at and is_structural_filler(record) and depth > 0:
            dropped["count"] += 1
            for child_index in range(min(child_count(node), MAX_CHILD_FANOUT)):
                visit(child_at(node, child_index), depth + 1,
                      path + [child_index], render_depth)
            return

        # 编号要**跨快照存活**，所以不能用 len(records) 这种遍历序号——
        # 插入一个元素就会把它后面所有元素的号全推走。见 StableIndexer。
        #
        # 发号必须在**确定这个节点真的进树之后**。原来它在两处裁剪之前，
        # 于是被裁掉的节点先领了号再被丢弃：号被消耗、却不进 records/refs。
        # 实测 Nautilus 一次快照 91 个元素、下标散布在 0..670——7 倍膨胀。
        # 而且被裁节点每次快照都重新领一批新号（它们不在 known 里），
        # 所以号会随会话单调涨下去，越涨越长、越涨越不像"树里的位置"。
        record["index"] = (indexer.index_for(path, record["controlType"],
                                             record["name"])
                           if indexer is not None else len(records))
        records.append(record)

        lines.append(render_element_line(record, render_depth, boxes=boxes))
        role = record["localizedControlType"] or record["controlType"] or "element"

        # 未展开的菜单：保留节点自身（它是 invoke_element_action 的入口），
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


# 像素比对的采样步长与阈值。
# 实测 1850x1053 窗口：抓一张 23ms，4px 步长比对 67ms、8px 步长 16ms。
# 取 8px——每个动作多约 40ms，相对 resolve_app 自己的 0.3s 可以忽略，
# 而 8px 足以看出一段文字挪位置、一个对话框开合。
PIXEL_DIFF_STRIDE = 8
PIXEL_DIFF_THRESHOLD = 30
# 判据不用"变化超过某个百分比"，用**持续性**——见 persistent_pixel_change。
#
# 走过两版弯路，都记下来：
#  1. 一开始设了个固定阈值 PIXEL_FAINT_PERCENT=0.05%，低于它就算"微弱"。
#     这是**猜的**，而 Playwright 恰恰拒绝猜：它的 toHaveScreenshot 把
#     maxDiffPixels / maxDiffPixelRatio **默认留空**交给调用方，对噪声源
#     采取的是消除（caret:"hide"、animations:"disabled" 都是默认值）。
#  2. 第二版改成"动作前连抓两张测噪声底"。方向对，但两张只隔几毫秒，
#     1Hz 的文本光标根本没来得及闪，噪声底测出来是 0——于是**空操作也被判成
#     "屏幕变了"**，变化区域正是那个 8x16 的光标。
#
# 现在照 Playwright"反复截图直到连续两张一致再比"的思路：动作后抓两张，
# 只认在两张里**都存在**的变化。闪烁的东西两张状态不同，自然被滤掉。
# 两张之间的间隔由建快照的耗时填上，几乎不额外增加延迟。


def capture_window_pixels(bounds):
    """抓窗口的原始像素，用于**前后比对**（不是给 agent 看的图）。

    存在的理由：树接不住整类效果。实测 LibreOffice Impress 上，右对齐生效、
    文件保存成功，**a11y 树都字节不变**，于是两个真的生效了的动作被判成
    "送达但被忽略"，agent 多花两步去自证（占那次 12 步的 17%）。

    像素是**独立于树**的第三种判据：树没变而屏幕变了，说明效果真实存在、
    只是树看不见；树没变屏幕也没变，才是"确实什么都没发生"的强证据。

    注意 `read_pixel_bytes()` 而不是 `get_pixels()`——后者在 PyGObject 上
    返回的是截断数据（实测 1850x1053 只拿到不到 100KB，正确值 5.85MB）。
    """
    if Gdk is None or bounds is None:
        return None
    try:
        screen = Gdk.Screen.get_default()
        if screen is None:
            return None
        pixbuf = Gdk.pixbuf_get_from_window(
            screen.get_root_window(),
            int(round(bounds["x"])), int(round(bounds["y"])),
            max(1, int(round(bounds["width"]))), max(1, int(round(bounds["height"]))))
        if pixbuf is None:
            return None
        return {
            "data": pixbuf.read_pixel_bytes().get_data(),
            "stride": pixbuf.get_rowstride(),
            "channels": pixbuf.get_n_channels(),
            "width": pixbuf.get_width(),
            "height": pixbuf.get_height(),
        }
    except Exception:
        return None


def pixel_change(before, after):
    """比出变化比例与变化区域；比不了就返回 None（**不假装比过**）。"""
    if not before or not after:
        return None
    if (before["width"] != after["width"] or before["height"] != after["height"]
            or before["stride"] != after["stride"]):
        # 窗口尺寸变了本身就是一种变化，但无法逐点比对——如实说明。
        return {"resized": True}
    a, b = before["data"], after["data"]
    stride, channels = before["stride"], before["channels"]
    width, height = before["width"], before["height"]
    changed = total = 0
    min_x = min_y = 1 << 30
    max_x = max_y = -1
    try:
        for y in range(0, height, PIXEL_DIFF_STRIDE):
            row = y * stride
            for x in range(0, width, PIXEL_DIFF_STRIDE):
                offset = row + x * channels
                total += 1
                delta = (abs(a[offset] - b[offset])
                         + abs(a[offset + 1] - b[offset + 1])
                         + abs(a[offset + 2] - b[offset + 2]))
                if delta > PIXEL_DIFF_THRESHOLD:
                    changed += 1
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
    except Exception:
        return None
    if total == 0:
        return None
    result = {"resized": False, "changed": changed, "total": total,
              "percent": 100.0 * changed / total}
    if max_x >= 0:
        result["box"] = (min_x, min_y, max_x - min_x + PIXEL_DIFF_STRIDE,
                         max_y - min_y + PIXEL_DIFF_STRIDE)
    return result


# 动作前后要比对的文本属性。**只挑格式类**，不是全量——全量里大半是
# writing-mode / variant 这种恒定值，比了也没信息。
#
# 这条能力是查 Playwright 时反推出来的：它的 aria snapshot 只渲染一小撮语义属性
# （[checked] [level=1]），而"查任意样式属性"是 `toHaveCSS` 这类**断言**的事——
# **快照是给 agent 看的摘要，断言是精确查询，两者不必是同一套字段。**
#
# 顺着这个思路回头查 AT-SPI，才发现 `Atspi.Text.get_default_attributes()`
# 一直都能读到 justification / size / weight / fg-color 这些东西。
# 我此前写进文档的"改段落对齐后 a11y 树字节不变"**是错的**——
# 树不变是因为**我们没渲染这些字段**，不是信息不存在。
# 实测取一次 0.10–0.41ms/节点（对比 record_for 在 GIMP 上是 8.45ms），近乎免费。
TEXT_ATTRIBUTES_OF_INTEREST = (
    "justification", "size", "weight", "style", "underline", "strikethrough",
    "family-name", "fg-color", "bg-color", "line-height", "indent",
    "left-margin", "right-margin", "text-decoration",
)


def text_attributes(node):
    """读一个节点的格式属性；读不到就返回 None（**不返回空字典冒充读到了**）。"""
    iface = safe(node.get_text_iface)
    if iface is None:
        return None
    raw = safe(lambda: Atspi.Text.get_default_attributes(iface))
    if not raw:
        return None
    try:
        table = dict(raw)
    except Exception:
        return None
    return {k: table[k] for k in TEXT_ATTRIBUTES_OF_INTEREST if k in table}


def persistent_pixel_change(before, after_one, after_two):
    """只认**在两张动作后画面里都存在**的变化。

    这是 Playwright 那条"反复截图直到连续两张一致再比"在桌面上的等价物。
    它解决的是我第一版栽的跟头：动作前连抓两张测噪声，但两张只隔几毫秒，
    1Hz 的文本光标根本没来得及闪，噪声底测出来是 0——于是**空操作的
    第二次 Ctrl+L 也被判成"屏幕变了"**，变化区域正是那个 8x16 的光标。

    闪烁的东西在两张动作后画面里状态不同（一亮一灭），所以"两张都相对
    动作前有差异"这个条件会把它滤掉；真正的界面变化则两张都在。

    两张之间的间隔由**建快照的时间**填上（resolve_app 自己就要 0.15–0.3s），
    所以除了多抓一张图（23ms）几乎不额外增加延迟。
    """
    if not before or not after_one or not after_two:
        return None
    frames = (before, after_one, after_two)
    if len({(f["width"], f["height"], f["stride"]) for f in frames}) != 1:
        return {"resized": True}
    a, b, c = before["data"], after_one["data"], after_two["data"]
    stride, channels = before["stride"], before["channels"]
    width, height = before["width"], before["height"]
    changed = total = 0
    min_x = min_y = 1 << 30
    max_x = max_y = -1
    try:
        for y in range(0, height, PIXEL_DIFF_STRIDE):
            row = y * stride
            for x in range(0, width, PIXEL_DIFF_STRIDE):
                offset = row + x * channels
                total += 1
                base = (a[offset], a[offset + 1], a[offset + 2])
                one = (abs(base[0] - b[offset]) + abs(base[1] - b[offset + 1])
                       + abs(base[2] - b[offset + 2])) > PIXEL_DIFF_THRESHOLD
                if not one:
                    continue
                two = (abs(base[0] - c[offset]) + abs(base[1] - c[offset + 1])
                       + abs(base[2] - c[offset + 2])) > PIXEL_DIFF_THRESHOLD
                if not two:
                    # 只在其中一张里变了——闪烁，不是效果。
                    continue
                changed += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    except Exception:
        return None
    if total == 0:
        return None
    result = {"resized": False, "changed": changed, "total": total,
              "percent": 100.0 * changed / total}
    if max_x >= 0:
        result["box"] = (min_x, min_y, max_x - min_x + PIXEL_DIFF_STRIDE,
                         max_y - min_y + PIXEL_DIFF_STRIDE)
    return result


def pixel_change_note(change):
    """把比对结果写成一句给 agent 看的话。`[pixels]` 前缀便于机器识别。"""
    if change is None:
        return None
    if change.get("resized"):
        return ("[pixels] The window changed size or position during this action, so a "
                "pixel comparison was not possible — but that change is itself evidence "
                "something happened.")
    if change["changed"] == 0:
        return ("[pixels] Nothing on screen stayed changed after this action: the window "
                "is pixel-identical to before it (transient flicker excluded).")
    box = change.get("box")
    where = ""
    if box:
        where = " Changes are concentrated in {{{},{},{},{}}} (window-relative pixels).".format(*box)
    return ("[pixels] {:.2f}% of the window changed on screen and STAYED changed "
            "across two captures ({} sample points).{} Transient things like a blinking "
            "text caret are filtered out by that second capture, so this is a real "
            "change rather than flicker.".format(
                change["percent"], change["changed"], where))


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
    include_screenshot=None,
    prune=True,
    boxes=False,
    known_refs=None,
):
    """构建应用快照。

    `include_screenshot=None` 表示"按当前策略决定"，见
    `a11y_screenshots_enabled()`；传 True/False 则强制。

    这里原本默认关闭，理由是"a11y 与 VLM 是两条独立轨道，a11y 轨不该顺带付
    截图的钱"。成本数字仍然成立（gedit 单次观测截图约 1014 token，占 35%，
    树裁剪后会升到 80% 左右）——**但"两条独立轨道"这个前提被实测推翻了**。

    实测 LibreOffice Impress：
    - 右对齐生效了，段落节点的 `Frame` 却一直指向占位符左端；
      `ctrl+r` 与 `ctrl+s` 都真的生效了，却都被"树没变"判成"送达但被忽略"。
      **两次假阴性，都是一张截图一眼判掉的。**
    - 反过来，「哪个 spin button 是 Position Y」靠截图只能凭标签的空间邻近去猜，
      靠树的 Description 是确定的。

    所以两条通道是**互补**，不是替代：树给可操作性，截图给可验证性。
    默认改为带截图，`#29` 的 A/B 用环境变量关掉再比。

    `boxes` 默认 **False**（对齐 Playwright 的同名开关）。几何实测占整棵树的
    35–50%，而它对 agent 是**冗余**的：截图恒带、且与树是同一个坐标空间，
    需要坐标时读图即可（已实测真实 agent 只靠截图命中了 a11y 树里零存在的
    GIMP 画布目标）。`click(element_index)` 的坐标由服务端内部从记录里取，
    不经过渲染，所以关掉几何**不削弱任何能力**。
    需要在树里直接看到矩形时传 `boxes: true`。
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
        boxes=boxes,
        indexer=StableIndexer(known_refs),
    )
    pid = node_pid(app)
    return {
        "app": {
            "name": node_name(app),
            "bundleIdentifier": node_name(app),
            "pid": pid,
        },
        "windowTitle": window_label(window, text_limit=text_limit),
        "windowBounds": bounds,
        "screenshotPngBase64": capture_window_png(bounds)
        if (a11y_screenshots_enabled() if include_screenshot is None else include_screenshot)
        else None,
        "treeLines": lines,
        "focusedSummary": focused_summary(pid, text_limit=text_limit),
        "selectedText": selected_text(pid, text_limit=text_limit),
        "elements": records,
        # 把 路径 -> (编号, role, name) 回给 Go，下次请求原样传回来，
        # 编号就能跨快照存活。见 StableIndexer。
        "refs": {
            ".".join(str(p) for p in r["runtimeId"]): {
                "index": r["index"], "role": r["controlType"], "name": r["name"],
            }
            for r in records
        },
        # 模态提示跟着快照走，而不是只在 get_app_state 里算一次：**动作后**的
        # 快照更需要它——正是那次点击弹出了对话框，那一刻说"你现在被挡住了"
        # 最有用。Go 侧的 appSnapshot 没有这个字段，encoding/json 会忽略它，
        # 运行时靠下面 pop 出来放进 notes。
        "modalNotes": modal_diagnostic(window, app) + foreign_foreground_note(app),
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


def name_fingerprint(text):
    """完整名字的短指纹。只在名字被截断、无法原样比对时才用得上。"""
    return hashlib.blake2b(str(text or "").encode("utf-8"),
                           digest_size=8).hexdigest()


def record_name_matches(node, record):
    """节点的名字是否等于 record 里存的那个——**两边用同一把尺子量**。

    存进 record 的 name 是 limit_text() 截断过的，而节点上读到的是完整的，
    直接比就必然失配；而失配是静默的，调用方只会看到身份判据悄悄退化。

    截断过的记录带 `nameHash`（完整名字的指纹），比的是完整名字，
    所以既不会因为截断而失配，也不会因为前缀相同而误判。
    """
    live = effective_name(node)
    fingerprint = record.get("nameHash")
    if fingerprint:
        return name_fingerprint(live) == fingerprint
    return live == str(record.get("name") or "")


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
        return record_name_matches(node, record)
    if effective_name(node):
        # 快照里没名字、现在有名字，说明换了个元素。
        # 这里也必须用 effective_name：借来的名字同样算"有名字"，
        # 否则一个借到名字的节点会被判成"仍然无名"，与 record_for 存进去的相反。
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
        # 与上面同一把尺子。这句原来是 `node_name(candidate) == target_name`，
        # 和 record_still_matches 里那句一模一样地错——同一个比较抄了两遍，
        # 于是同一个 bug 也有两份。
        if target_name and node_role(candidate) == target_role \
                and record_name_matches(candidate, record):
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
        if normalized_action(lower) in NON_SELF_ACTIONS:
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


def window_relative(window_bounds, x, y):
    """把 screen_point 算出的屏幕绝对坐标换回窗口相对——**agent 只认后者**。

    树里的 {x,y,w,h}、截图、click_xy 的入参全是窗口相对，只有 XTEST 需要绝对
    坐标。Note 里报哪一个不是风格问题：click_xy 那条 Note 明文写着 "in
    window-relative pixels — the same coordinate space as the attached
    screenshot and as the Frame values in the tree"，等于把约定立死了。

    实测踩到过：Nautilus 的图标在树里是 {256,76,78,68}（中心 295,110），
    而合成右键的 Note 报 (384,159)——差的正是窗口原点 (89,49)。agent 照着
    这个数去 click_xy，会偏出整整一个窗口原点。
    """
    if window_bounds is None:
        return x, y
    return x - window_bounds["x"], y - window_bounds["y"]


# 稳定判据：两次采样一致即认为停住了。Playwright 等的是"连续两个动画帧内
# bounding box 不变"，这里是同一件事的桌面版。
STABLE_SAMPLE_SECONDS = 0.08


def stable_timeout_seconds():
    """等元素停住的上限，可调。

    默认 1 秒是折中：够长，能盖住实测里绝大多数一次性重排；够短，遇到永远在
    动的东西（进度条、动画）也不会把一次点击拖成几秒。动画重的应用可以调大，
    追求最低延迟的场景可以调小到 0——那等于关掉这项等待。
    """
    raw = os.environ.get("OPEN_COMPUTER_USE_STABLE_TIMEOUT_MS", "").strip()
    if not raw:
        return 1.0
    try:
        return max(0.0, float(raw) / 1000.0)
    except ValueError:
        return 1.0


def wait_until_stable(element, window_bounds, first=None):
    """等元素停止移动再动手。返回 (最终 frame, 采样次数, 是否稳定下来)。

    **只做 stable，不做 enabled。** Playwright 的可操作性检查里还有"等元素
    enabled"，那一条在 Linux 上不能照搬：本仓库实测 Nautilus 的文件图标根本
    不设 ENABLED / SENSITIVE，状态里只有 SHOWING / VISIBLE / FOCUSABLE，却带着
    open / menu 两个动作、完全可点。拿 ENABLED 当门禁会让 agent 直接跳过可用
    目标。宁可不等这一项，也不能用一个不可靠的信号去拦住真实可用的元素。
    同理不做 "receives events" 的命中测试门禁——本仓库实测命中率
    gedit 11/11、Nautilus 19/25、LibreOffice 12/25，拿它当门禁会拦掉一半
    LibreOffice 的正常点击。

    `first` 让调用方把已经取过的那次采样传进来，省掉一次重复读取。
    """
    if element is None:
        return None, 0, True
    previous = first if first is not None else safe(
        lambda: relative_frame(element, window_bounds)
    )
    samples = 1
    deadline = time.monotonic() + stable_timeout_seconds()
    while True:
        time.sleep(STABLE_SAMPLE_SECONDS)
        current = safe(lambda: relative_frame(element, window_bounds))
        samples += 1
        if current is None or previous is None or same_frame(previous, current):
            return current or previous, samples, True
        previous = current
        if time.monotonic() >= deadline:
            # 超时**不阻止动作**：一个一直在动的元素（进度条、动画）照样可能
            # 是正确的目标。这里只把事实说出来，让 agent 自己判断。
            return current, samples, False


def current_geometry(element, element_record, window_bounds):
    """把记录里的几何换成**活节点此刻的位置**，并说明它挪了多少。

    Playwright 的 locator 在每次动作时重新解析，这是同一件事的桌面版。
    我们其实早就在 find_element 里重新解析出了活节点，却仍然拿 Go 缓存里的
    那份旧记录去算坐标——于是同一次调用里有两套事实：语义路径作用在活节点上，
    合成路径打在快照拍摄时的位置上。

    实测：VS Code 里由外部收起侧栏后，click(element_index=21) 把坐标合成在
    (572,38)，正是缓存框 {547,28,50,19} 的中心，而界面那时已经重排。

    桌面比浏览器更需要这一条：这里没有"页面加载完成"这种事件，界面可以在
    两次工具调用之间被动画、异步加载、甚至**别的进程**移动。

    返回 (记录, 位移说明)。活节点取不到位置时原样退回旧记录——宁可用旧坐标，
    也好过没有坐标。
    """
    if element is None or not element_record:
        return element_record, None
    live = safe(lambda: relative_frame(element, window_bounds))
    if live is None:
        return element_record, None
    live, samples, settled = wait_until_stable(element, window_bounds, first=live)
    if live is None:
        return element_record, None
    old = element_record.get("frame")
    updated = dict(element_record)
    updated["frame"] = live
    if not old:
        return updated, None
    dx = live["x"] - old["x"]
    dy = live["y"] - old["y"]
    # 稳定与否都要说采样次数。只在"不稳定"时才说，等于把"我等过、等到了"
    # 这条信息藏起来——而那正是 agent 判断该不该信任这次坐标的依据。
    unsettled = (
        " Waited for it to hold still first: {} position samples {:.0f}ms apart, "
        "and it settled.".format(samples, STABLE_SAMPLE_SECONDS * 1000)
        if settled
        else (
            " The element was STILL MOVING when this action ran: across {} samples "
            "{:.0f}ms apart it never held the same position. The click went to wherever "
            "it was at that instant, which may not be where it ended up.".format(
                samples, STABLE_SAMPLE_SECONDS * 1000
            )
        )
    )
    if abs(dx) <= 2 and abs(dy) <= 2:
        # 1–2 像素的抖动是子像素取整，不是移动。位置没变时也不必报"等稳了"，
        # 那是每次动作都会出现的常态，只会淹没真正的信号。
        return updated, (None if settled else unsettled.strip())
    return updated, (
        "The element MOVED since the snapshot: it was at {{{:.0f},{:.0f}}} and is now "
        "at {{{:.0f},{:.0f}}} (window-relative). This action used its CURRENT position, "
        "not the one in the tree you read. Any other coordinate you took from that "
        "snapshot is stale by the same amount.".format(
            old["x"], old["y"], live["x"], live["y"]
        )
        + unsettled
    )


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
    # 裸坐标**夹紧在窗口矩形内**。
    #
    # 这替换掉了原来的 OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS 闸门。
    # 那道闸门防的是"把指针甩到屏幕上任意一点"，做法是**整个禁掉**无锚点的坐标
    # 点击——代价是 GUI 通道变成默认不可用，而实测有多处 AT-SPI 动作返回成功却
    # 不生效，此时坐标是唯一出路。
    #
    # 夹紧是更强的保证：它把风险从"可能打到别的应用"直接降为"最多打到本窗口的
    # 边缘"，同时**不牺牲任何能力**。GUI 通道是一条声明过的一等通道，
    # 不该靠环境变量才能用。
    left, top = window_bounds["x"], window_bounds["y"]
    right = left + max(window_bounds["width"] - 1, 0)
    bottom = top + max(window_bounds["height"] - 1, 0)
    return (
        min(max(left + float(x), left), right),
        min(max(top + float(y), top), bottom),
    )


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
    # 有些窗口**永远不设 ACTIVE**，此时向 X11 求证一次。
    if x11_holds_focus(window):
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


def x11_holds_focus(window):
    """向 X11 求证：焦点是不是落在**目标应用自己**的某个窗口上。

    为什么需要这条旁证，以及为什么判据是"同进程"而不是"同窗口"：

    GNOME 的门户文件对话框（xdg-desktop-portal-gnome，每一次"另存为/打开"
    都是它）状态位是 **MODAL + VISIBLE，既没有 ACTIVE 也没有 SHOWING**。
    更糟的是它的二级对话框——实测那句"文件已存在，是否替换？"——
    **在 a11y 树里根本不存在**（门户应用只报了一个窗口），却握着输入焦点。

    于是焦点守卫只认 ACTIVE 时，整条 GUI 通道在这类对话框上直接不可用：
    实测 OSWorld 第 4 题（网页存 PDF 到桌面）就卡死在这里，click_xy 被拒，
    理由是"树里没有任何窗口报告 ACTIVE"——而那个理由是对的，只是结论太绝。

    判据用 **_NET_WM_PID 与目标应用的 pid 相等**：
      - 不能比窗口标题，那个二级对话框**没有标题**；
      - 不能要求同一个窗口，正是它挡在前面；
      - 同进程已经足够安全：守卫要防的是"输入打进别的应用"，
        而焦点在目标应用自己的子对话框上时，这个风险不存在。

    本仓库早先试过一次 X11 焦点层并撤回过，理由是实测 X11 与 AT-SPI 的 ACTIVE
    在同一瞬间翻转、加了等于没加。那个结论在这里不适用也不矛盾：那次比的是
    "两者都会翻转、谁更快"，这里是"AT-SPI 根本不翻转"。所以这一层只在 AT-SPI
    说不出话时兜底。
    """
    app = safe(lambda: Atspi.Accessible.get_application(window))
    target_pid = node_pid(app) if app is not None else 0
    if not target_pid:
        return False
    try:
        active = subprocess.run(["xdotool", "getactivewindow"],
                                capture_output=True, text=True, timeout=3)
        if active.returncode != 0 or not active.stdout.strip():
            return False
        prop = subprocess.run(
            ["xprop", "-id", active.stdout.strip(), "_NET_WM_PID"],
            capture_output=True, text=True, timeout=3)
    except Exception:
        return False
    if prop.returncode != 0:
        return False
    digits = "".join(ch for ch in prop.stdout if ch.isdigit() or ch == " ").split()
    if not digits:
        return False
    return int(digits[-1]) == int(target_pid)


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


# 一"页"折算成多少次滚轮。滚轮一格通常是 3 行，这里取 5 格 ≈ 15 行。
# 这是**近似**，真实距离取决于应用自己的滚动设置——所以 Note 里要说是近似，
# 不能让 agent 以为 pages=1 精确等于一次 Page_Down。
WHEEL_CLICKS_PER_PAGE = 5


def scroll_element(direction, pages, point=None):
    """滚动。有元素坐标就用滚轮按位置滚，没有就退回往焦点发 Page 键。

    此前这里**只有**后一条路：element_index 是必填参数却完全不参与，键盘
    事件落到当前焦点上。工具描述里如实标注了这一点——但如实说明一个缺陷
    不等于修好它。

    滚轮是真的按位置生效，实测（gedit 打开 600 行文件，在文本区中心发 6 次
    b5c）：
        对照组（什么都不做）  文本区 0% 像素变化
        实验组（滚轮 x6）     文本区 23% 像素变化

    要说清它**滚的是谁**：滚轮作用在指针下方的可滚动祖先上，不一定是你指定
    的那个元素本身。这正是"在这个位置滚"的语义，但和"滚动这个控件"不完全
    等同，Note 里要讲明白。

    横向滚动仍然走 Left/Right 键：滚轮的横向按钮（b6/b7）我没有实测过，
    不测就不发——这个仓库里没测过的路径不该以"应该能work"的名义上线。

    返回实际走的路线，让 Note 说实话。
    """
    repeat = max(1, int(math.ceil(float(pages or 1))))
    horizontal = direction in ("left", "right")
    if point is not None and not horizontal:
        x, y = point
        Atspi.generate_mouse_event(int(round(x)), int(round(y)), "abs")
        event = "b4c" if direction == "up" else "b5c"
        for _ in range(repeat * WHEEL_CLICKS_PER_PAGE):
            Atspi.generate_mouse_event(int(round(x)), int(round(y)), event)
            time.sleep(0.03)
        return "wheel"

    key = "Page_Down"
    if direction == "up":
        key = "Page_Up"
    elif direction == "left":
        key = "Left"
    elif direction == "right":
        key = "Right"
    for _ in range(repeat):
        send_key(key)
        time.sleep(0.04)
    return "keys"


# 每条动作 Note 都带**两个正交的标签**，因为这是两件不同的事，
# 混成一个会同时丢掉两边的信息。
#
# 【寻址轴】目标是**怎么定位到的**——决定了该拿什么去验证它：
#   [a11y]      通过无障碍树的 element_index 定位
#   [gui]       通过屏幕坐标定位，树完全没有参与
#   [keyboard]  通过当前输入焦点定位，两条通道都没参与
#
# 【执行轴】定位好之后**用什么把动作发出去**：
#   [semantic]   AT-SPI 语义动作 / 可编辑文本 API
#   [synthesis]  XTEST 全局合成
#
# 两个轴会交叉。最要紧的一格是 `[a11y][synthesis]`：**用树给的坐标去合成点击**
# ——寻址是 a11y 的，执行是合成的。以前只有执行轴一个标签，这一格会被记成
# 纯合成，于是"agent 压根没用 a11y"和"用了但语义动作失效后回落"就分不开了。
A11Y_CHANNEL = "[a11y]"
GUI_CHANNEL = "[gui]"
KEY_CHANNEL = "[keyboard]"
SEMANTIC = "[semantic] "
SYNTHESIS = "[synthesis] "


# 坐标命中测试最多往下钻几层。AT-SPI 的 get_accessible_at_point 每次只返回
# **直接子节点**，要拿到叶子必须自己递归。给个上限，别在环形结构上转不出来。
HIT_TEST_MAX_DEPTH = 25


def element_at_point(window, x, y):
    """报出屏幕坐标 (x, y) 上的可访问元素。**只是提示，不是保证。**

    裸坐标点击目前是个纯盲点：工具打完就走，说不出打到了什么。有了这个
    至少能把"你以为点的"和"实际落在哪"对上一次。

    但准确率**因工具包而异**，实测（递归到叶子，拿元素自身中心点回测）：
        gedit(GTK) 11/11   Nautilus(GTK) 19/25
        LibreOffice(VCL) 12/25   VLC(Qt) 2/25
    VCL 与 Qt 上经常只解析到容器层。所以这条信息必须**明确标成提示**发出去，
    不能让 agent 拿它当"我点中了正确的东西"的证据——那会用一个新的谎
    去替换旧的沉默。
    """
    node = window
    for _ in range(HIT_TEST_MAX_DEPTH):
        component = safe(node.get_component_iface)
        if component is None:
            break
        child = safe(
            lambda: Atspi.Component.get_accessible_at_point(
                component, int(round(x)), int(round(y)), Atspi.CoordType.SCREEN
            )
        )
        if child is None:
            break
        node = child
    if node is window:
        return None
    return node


def describe_hit(window, x, y):
    """把命中结果写成一句可以直接拼进 Note 的话；测不出来就返回空串。"""
    node = safe(lambda: element_at_point(window, x, y))
    if node is None:
        return (
            " Hit test could not identify any element under that point, so this click "
            "is unverified: it may have landed on nothing, or on something this "
            "toolkit does not expose."
        )
    role = node_role(node)
    name = node_name(node)
    label = "{} {!r}".format(role, name) if name else role
    return (
        " Hit test says the element under that point is {} — treat this as a HINT, not "
        "proof: AT-SPI hit testing resolves only to a container on some toolkits "
        "(measured accurate 11/11 on GTK gedit but 12/25 on LibreOffice). If it names "
        "something other than your intended target, re-read the tree before "
        "continuing.".format(label)
    )


def addressing_channel(element_record):
    """这次动作的目标是靠树定位的，还是靠裸坐标定位的。"""
    return A11Y_CHANNEL if element_record else GUI_CHANNEL

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


def resolved_note(element, element_record):
    """说清这次动作**作用在了谁身上**。

    此前动作 Note 只说"做了什么"，不说"对谁做的"。传选择器时尤其要紧：
    `push button "Save"` 最终解析到哪个节点，调用方完全看不见——而解析是
    多级回退的（runtimeId 路径 → automationId → name+role → role+几何），
    退到第几级、退到了谁身上，都不透明。

    报的是**活节点**读到的 role 与 name，不是快照记录里那份。两者不一致时
    单独警告：那意味着按路径解析到的已经不是原来那个控件了，正是本仓库
    反复修过的那类静默走偏——record_still_matches 里记着 Nautilus 的实例：
    菜单关掉之后同一个 index 9 解析到了工具栏的"切换视图选项"，
    于是"重命名"变成了别的操作，而且一路 isError=False。

    """
    if element is None or not element_record:
        return None
    index = element_record.get("index")
    live_role = str(node_role(element) or "")
    live_name = str(effective_name(element) or "")
    if live_name:
        shown = "{} {!r}".format(live_role, live_name)
    else:
        shown = "an unnamed {}".format(live_role or "element")
    note = "Resolved element_index {} to {}.".format(index, shown)

    was_role = str(element_record.get("controlType") or "")
    was_name = str(element_record.get("name") or "")
    drifted = []
    if was_role and was_role != live_role:
        drifted.append("role was {!r}, now {!r}".format(was_role, live_role))
    if was_name and was_name != live_name:
        drifted.append("name was {!r}, now {!r}".format(was_name, live_name))
    if drifted:
        note += (
            " WARNING: this no longer matches the snapshot you read ({}). The "
            "reference resolved to a DIFFERENT control than the one you picked; "
            "re-read the tree before trusting this action.".format("; ".join(drifted))
        )
    return note


# 前置窗口是不是"挡路的对话框"，分两档——因为**证据强度不同**。
DIALOG_ROLES = {"dialog", "alert", "file chooser", "color chooser"}


def window_label(window, text_limit=DEFAULT_TEXT_LIMIT):
    """窗口标题。没有名字时给一个**能用的**替代，而不是空串。

    实测（跑 OSWorld 第 3 题时从 trace 里看出来的）：agent 在 Chrome 的菜单
    之间穿行时，有 4 个动作的窗口标题是空的——`Window: ""`。菜单、下拉、
    popup 这类窗口很多都不设名字，而窗口标题是 agent 判断"我现在在哪儿"
    的第一信号，空串等于把这个信号删掉。

    退而求其次的顺序：自己的名字 → 角色 + 第一个有名字的子节点 → 只有角色。
    子节点那一档是有用的：一个无名 `menu` 下面第一项常常是
    `menu item "Recently closed"`，足以让调用方认出这是什么菜单。
    """
    name = str(node_name(window) or "").strip()
    if name:
        return limit_text(name, text_limit=text_limit)
    role = str(node_role(window) or "window")
    for index in range(min(child_count(window), 8)):
        child = safe(lambda: window.get_child_at_index(index))
        if child is None:
            continue
        hint = str(effective_name(child) or "").strip()
        if hint:
            return limit_text(
                "(unnamed {} containing {!r})".format(role, hint),
                text_limit=text_limit)
    return "(unnamed {})".format(role)


def foreign_foreground_note(app):
    """前台窗口属于**别的进程**时，指名道姓地说出来。

    实测（OSWorld 第 4 题，"把网页存成 PDF 放到桌面"）：在 Chrome 里点 Save，
    弹出来的文件保存对话框属于 `xdg-desktop-portal-gnome`（pid 1343），
    和 Chrome（pid 186930）**完全是两个进程**。现代 GNOME 上，走门户的应用
    其文件对话框都是这样。

    后果很硬：`get_app_state(app="chrome")` 永远看不到那个对话框，Chrome 的
    窗口列表里也没有它——我实测确认 Chrome 的 a11y 应用下只有一个窗口。
    agent 会看到"点了 Save，什么都没发生"，然后开始怀疑自己点错了。

    它确实出现在 list_apps 里，但那要求 agent 先想到去列一遍。与其指望它想到，
    不如在动作之后直接说：现在前台的是谁、该去问哪个 app。
    """
    if app is None:
        return []
    mine = str(node_name(app) or "")
    desktop = safe(lambda: Atspi.get_desktop(0))
    if desktop is None:
        return []
    for index in range(min(child_count(desktop), MAX_CHILD_FANOUT)):
        other = safe(lambda: desktop.get_child_at_index(index))
        if other is None:
            continue
        name = str(node_name(other) or "")
        if not name or name == mine:
            continue
        for window_index in range(min(child_count(other), 12)):
            window = safe(lambda: other.get_child_at_index(window_index))
            if window is None:
                continue
            # 判据不能只认 ACTIVE+SHOWING。实测那个门户对话框的状态位是
            # **MODAL + VISIBLE，既没有 ACTIVE 也没有 SHOWING**——第一版就是
            # 因为要求 ACTIVE+SHOWING 而完全不触发。这和本仓库反复记过的那条
            # 是同一件事：状态位设不设，跨工具包全凭自觉，只能从"存在"推语义，
            # 不能从"缺失"推语义。
            in_front = state_contains(window, Atspi.StateType.MODAL) or (
                state_contains(window, Atspi.StateType.ACTIVE)
                and state_contains(window, Atspi.StateType.SHOWING))
            if not (in_front and state_contains(window, Atspi.StateType.VISIBLE)):
                continue
            title = str(node_name(window) or "").strip() or "(untitled)"
            return [
                "ANOTHER APP IS IN FRONT: the active window is now {!r}, owned by "
                "{!r} — a DIFFERENT process from {!r}. The tree below is still {!r}'s, "
                "so it does not contain that window at all. This is normal on GNOME: "
                "file open/save dialogs belong to xdg-desktop-portal-gnome, not to the "
                "app that asked for them. Call get_app_state with app={!r} to see it.".format(
                    title, name, mine, mine, name)
            ]
    return []


def modal_diagnostic(window, app):
    """选中的窗口是挡在前面的对话框时，**明说出来**。

    为什么需要：一个动作把对话框弹了出来，下一次 get_app_state 返回的就是
    对话框的树——窗口标题变了、下标全部重排，但这些都是**间接**信号。
    agent 完全可能继续用上一份快照里主窗口的 element_index，然后困惑于
    "为什么调用成功了却什么都没发生"。

    这条功能第一次跑起来就当场解释了一个我卡了二十分钟的现象：LibreOffice
    的 pptx 怎么都打不开，因为有一个 `Question`（"Document in Use"）对话框
    一直挡着，而窗口列表里看不出来。

    **分两档说，因为证据强度不同：**

      MODAL 位存在   → 可以断言"应用会忽略其它窗口的输入"。这是 AT-SPI
                      对模态的定义，有它才能这么说。
      只是对话框在前 → 只能说"树是对话框的，不是主窗口的"。

    第二档不是保守过头，是实测逼出来的：LibreOffice 7.3 的「Tip of the Day」
    是 role=dialog、ACTIVE、SHOWING，**却不设 MODAL**，而它确确实实挡在
    应用前面。MODAL 位在 Linux 上和 ENABLED 一样，不同工具包设不设全凭自觉。
    只认 MODAL 会漏掉真实的阻塞；把两档混为一谈则会替不设 MODAL 的对话框
    打一个我们无权打的包票。
    """
    if window is None:
        return []
    is_modal = state_contains(window, Atspi.StateType.MODAL)
    role = str(node_role(window) or "").lower()
    if not is_modal and role not in DIALOG_ROLES:
        return []

    title = str(node_name(window) or "").strip() or "(untitled)"
    others = []
    count = (safe(lambda: app.get_child_count(), 0) or 0) if app is not None else 0
    for index in range(min(count, MAX_CHILD_FANOUT)):
        other = safe(lambda: app.get_child_at_index(index))
        if other is None or other == window:
            continue
        if state_contains(other, Atspi.StateType.SHOWING):
            name = str(node_name(other) or "").strip()
            if name and name != title:
                others.append(name)
    if not is_modal and not others:
        # 只有这一个窗口，那它就是主界面，不是"挡在什么前面"。
        return []

    behind = ""
    if others:
        behind = " Behind it: {}.".format(", ".join(sorted(set(others))[:3]))

    if is_modal:
        return [
            "MODAL DIALOG: the tree below is {!r}, a modal dialog, NOT the app's main "
            "window.{} The application will ignore input to every other window until "
            "this one is dismissed, so any element_index you took from an earlier "
            "snapshot of the main window addresses nothing right now. Finish or cancel "
            "this dialog first, then call get_app_state again for fresh indices.".format(
                title, behind
            )
        ]
    return [
        "DIALOG IN FRONT: the tree below is {!r}, a dialog, NOT the app's main "
        "window.{} It does not report the MODAL state, so this is not proof that the "
        "app is blocked — but the indices below belong to the dialog, and any "
        "element_index from an earlier snapshot of the main window does not address "
        "anything in it. Measured example of exactly this: LibreOffice's Tip of the "
        "Day sits in front while reporting no MODAL state.".format(title, behind)
    ]


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
        # 只渲染一层树，避免为了拿一张图顺带付整棵树的钱。
        # `get_app_state` 现在默认也带图（见 a11y_screenshots_enabled），所以这里
        # 不再是"VLM 轨道的唯一入口"，而是**只要图不要树**时的便宜入口。
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
            boxes=bool(operation.get("boxes", False)),
            known_refs=operation.get("knownRefs"),
            # None = 按默认策略（带图）。`find` 与 `verify` 会显式传 False：
            # 前者是查询、后者是断言，都不是"观测"，不该付视觉 token
            # ——基线里一次观测的视觉部分是 5120 token，而 verify 会**轮询**，
            # 带图的话一次断言就能烧掉十几次观测的钱。
            include_screenshot=operation.get("includeScreenshot"),
        )
        response = {"ok": True, "snapshot": snapshot}
        # 模态提示排在最前：它决定了下面整棵树该怎么读。
        diagnostics = snapshot.pop("modalNotes", []) + snapshot_diagnostics(
            snapshot.get("elements") or []
        )
        if diagnostics:
            response["notes"] = diagnostics
        return response

    app = resolve_app(operation.get("app", ""))
    _, window = main_window(app)
    bounds = operation.get("windowBounds") or extents(window)
    # 动作前抓一张像素，动作后抓两张——只认在两张里都持续存在的变化。
    # 详见 persistent_pixel_change：这是 Playwright"连续两张一致再比"在桌面上的
    # 等价物，用来滤掉闪烁的文本光标（实测它会把空操作误判成"屏幕变了"）。
    pixels_before = safe(lambda: capture_window_pixels(bounds))
    element_record = operation.get("element")
    element = find_element(app, element_record)
    # 重新解析出活节点之后，**立刻**把几何也刷新成它当前的位置。
    # 不这么做的话，语义路径用活节点、合成路径用旧几何，是同一次调用里的两套事实。
    element_record, moved_note = current_geometry(element, element_record, bounds)
    # 动作之前先记下顶层窗口集合。动作之后要靠它判断有没有开出/关掉窗口，
    # 从而决定该不该多等焦点落定——详见 wait_for_ui_to_settle。
    windows_before = safe(lambda: window_identity_set(app))
    # 每个动作都要说清楚实际走了哪条路径、结果有没有被校验过。返回一棵新的
    # accessibility tree 看着像执行确认，其实只是快照，不能当成动作生效的证据。
    notes = []
    # "解析到了谁"排在所有动作 Note 之前：先说对谁做，再说做了什么。
    resolved = resolved_note(element, element_record)
    if resolved:
        notes.append(A11Y_CHANNEL + " " + resolved)
    if moved_note:
        # 通道标签后面要有空格，否则渲染成 `[a11y]The element MOVED`。
        notes.append(A11Y_CHANNEL + " " + moved_note)

    if tool == "click_xy":
        # GUI 通道：纯坐标，树完全没有参与定位。
        x, y = screen_point(bounds, None, operation.get("x"), operation.get("y"))
        require_window_focus(window, "click_xy")
        send_mouse_click(
            x, y, operation.get("mouse_button", "left"), operation.get("click_count", 1)
        )
        notes.append(
            GUI_CHANNEL
            + SYNTHESIS
            + "Synthesized a coordinate click at ({:.0f}, {:.0f}) in window-relative "
            "pixels — the same coordinate space as the attached screenshot and as the "
            "Frame values in the tree. {}{}".format(
                operation.get("x"), operation.get("y"),
                UNVERIFIED_SYNTHESIS,
                describe_hit(window, x, y),
            )
        )
    elif tool == "click":
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
                A11Y_CHANNEL
                + SEMANTIC
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
                addressing_channel(element_record)
                + SYNTHESIS
                + "Synthesized a coordinate click at ({:.0f}, {:.0f}) in window-relative "
                "pixels after bringing the window to the foreground. {}{}".format(
                    *window_relative(bounds, x, y),
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
                    A11Y_CHANNEL
                    + SEMANTIC
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
                        "a coordinate click at ({:.0f}, {:.0f}) in window-relative "
                        "pixels. ".format(*window_relative(bounds, x, y))
                    )
                else:
                    reason = (
                        "No usable AT-SPI action was available, so this fell back to a "
                        "coordinate click at ({:.0f}, {:.0f}) in window-relative pixels "
                        "after bringing the window to the foreground. ".format(
                            *window_relative(bounds, x, y)
                        )
                    )
                notes.append(
                    addressing_channel(element_record)
                    + SYNTHESIS
                    + reason
                    + UNVERIFIED_SYNTHESIS
                )
        else:
            raise RuntimeError("Invalid click_method '{}'".format(click_method))
    elif tool == "invoke_element_action":
        action = operation.get("action", "")
        # 动作名声明了目标状态时，动作后回读校验——照 Playwright 的 _setChecked。
        states_before = safe(lambda: readable_states(element))
        # 只有"开右键菜单"这类幂等动作才做校验+回落。做不到这一点的话，
        # 一个已经生效但观测不到的破坏性动作会被重复执行。
        opens_menu = str(action).lower() in CONTEXT_MENU_ACTIONS
        had_menu = context_menu_visible(app) if opens_menu else False
        invoke_secondary_action(element, action)
        transition, failed = state_transition_note(
            action, states_before, safe(lambda: readable_states(element)))
        if transition and failed:
            # Playwright 在这里抛的是 NonRecoverableDOMError——不重试，直接抛。
            # 重复同一个动作不会有不同结果。
            raise RuntimeError(transition)
        notes.append(
            A11Y_CHANNEL
            + SEMANTIC
            + "Invoked the '{}' AT-SPI action. ".format(action)
            + (transition if transition else UNVERIFIED_SEMANTIC)
        )
        if opens_menu and not had_menu:
            time.sleep(MENU_SETTLE_SECONDS)
            if not context_menu_visible(app):
                # 实测 Nautilus：文件图标的 `menu` 动作**永远返回成功、永远不开
                # 菜单**，与是否聚焦/选中无关。语义通道在这里是死路，只能合成。
                #
                # 兜底有两条路，顺序是**实测定的，不是猜的**：
                #
                #   合成右键（button 3）在这台机器的 Nautilus 上 100% 失效。
                #   用 xdotool 绕开本项目直接发同样的右键，一样开不出菜单；
                #   拆成 mousedown/mouseup 分开发也一样。而同一位置的**左键**
                #   立刻生效（图标变 [focused]、状态栏出现 "…" selected），
                #   所以既不是坐标错也不是窗口没焦点——就是 button 3 这条路
                #   在这个 GTK4 版本上不通。
                #
                #   Shift+F10 一次就开出了 11 个 menu item。它本来就是无障碍
                #   标准的上下文菜单入口，比合成右键更该排在前面。
                #
                # 所以：先左键选中（上下文菜单必须作用在选中项上），再 Shift+F10，
                # 都不行才退回合成右键——保留它是因为它在别的工具包上是通的，
                # 删掉等于用一个应用的证据去否掉另一些应用的唯一出路。
                x, y = screen_point(bounds, element_record, None, None)
                require_window_focus(window, "invoke_element_action")
                send_mouse_click(x, y, "left", 1)
                time.sleep(MENU_SETTLE_SECONDS)
                send_key("shift+F10")
                time.sleep(MENU_SETTLE_SECONDS)
                if context_menu_visible(app):
                    notes.append(
                        A11Y_CHANNEL
                        + SYNTHESIS
                        + "The '{}' action reported success but no menu appeared, so "
                        "this selected the element at ({:.0f}, {:.0f}) in "
                        "window-relative pixels and opened the context menu with "
                        "Shift+F10 — the accessibility route, which is measurably more "
                        "reliable here than a synthesized right-click. A menu is now "
                        "visible in the tree.".format(
                            action, *window_relative(bounds, x, y)
                        )
                    )
                else:
                    send_mouse_click(x, y, "right", 1)
                    notes.append(
                        A11Y_CHANNEL
                        + SYNTHESIS
                        + "The '{}' action reported success but no menu appeared, and "
                        "neither did Shift+F10, so this fell back to a synthesized "
                        "right-click at ({:.0f}, {:.0f}) in window-relative pixels — "
                        "the same space as the tree's {{x,y,w,h}} and the screenshot. "
                        "{}".format(
                            action, *window_relative(bounds, x, y), UNVERIFIED_SYNTHESIS
                        )
                    )
    elif tool == "scroll":
        require_window_focus(window, "scroll")
        direction = operation.get("direction", "down")
        pages = operation.get("pages", 1)
        # 有元素几何就按位置滚。element_index 从"必填但没人用"变成真的参与定位。
        point = None
        if element_record and element_record.get("frame"):
            point = screen_point(bounds, element_record, None, None)
        route = scroll_element(direction, pages, point)
        if route == "wheel":
            wx, wy = window_relative(bounds, *point)
            notes.append(
                A11Y_CHANNEL
                + SYNTHESIS
                + "Scrolled with {} wheel notch(es) at ({:.0f}, {:.0f}) in "
                "window-relative pixels — the position came from element_index, so "
                "this scroll IS targeted. Two caveats. First, the wheel acts on the "
                "scrollable ancestor under that point, which is not necessarily the "
                "element you named. Second, one page is APPROXIMATED as {} notches; "
                "the real distance is whatever the application's scroll settings say, "
                "so do not treat pages=1 as exactly one Page_Down. This also moves the "
                "real pointer. {}".format(
                    int(math.ceil(float(pages or 1))) * WHEEL_CLICKS_PER_PAGE,
                    wx,
                    wy,
                    WHEEL_CLICKS_PER_PAGE,
                    UNVERIFIED_SYNTHESIS,
                )
            )
        else:
            notes.append(
                KEY_CHANNEL
                + SYNTHESIS
                + "Scrolled by synthesizing page keys after bringing the window to the "
                "foreground. NOTE: element_index did NOT target this scroll — the keys "
                "go to whatever widget currently holds focus inside the window. This "
                "route is used when the element has no usable geometry, and for "
                "horizontal scrolling, whose wheel buttons this project has not "
                "measured. If the wrong region scrolled, focus that region first "
                "(click it) and scroll again. {}".format(UNVERIFIED_SYNTHESIS)
            )
    elif tool == "drag_xy":
        from_x, from_y = screen_point(
            bounds, None, operation.get("from_x"), operation.get("from_y")
        )
        to_x, to_y = screen_point(bounds, None, operation.get("to_x"), operation.get("to_y"))
        require_window_focus(window, "drag_xy")
        send_drag(from_x, from_y, to_x, to_y)
        notes.append(
            GUI_CHANNEL
            + SYNTHESIS
            + "Synthesized a coordinate drag after bringing the window to the "
            "foreground. {} A screenshot is attached because the accessibility "
            "tree does not reflect drag results: measured on LibreOffice Impress, "
            "moving a title from 0.76cm to 15.00cm left the element's Frame in the "
            "tree completely unchanged. Judge this drag from the image, not the "
            "tree.".format(UNVERIFIED_SYNTHESIS)
        )
    elif tool == "type_text":
        # AT-SPI 直写不依赖窗口焦点，优先走；只有退化到全局合成时才需要夺焦点。
        written, before_chars, after_chars = insert_text_detail(
            window, operation.get("text", "")
        )
        if written:
            notes.append(
                A11Y_CHANNEL
                + SEMANTIC
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
                KEY_CHANNEL
                + SYNTHESIS
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
            KEY_CHANNEL
            + SYNTHESIS
            + "Synthesized '{}' after bringing the window to the foreground. {}".format(
                operation.get("key", ""), UNVERIFIED_SYNTHESIS
            )
        )
    elif tool == "set_value":
        if element is None:
            raise RuntimeError("unknown element_index")
        value = operation.get("value", "")
        if not set_element_value(element, value):
            # 语义写值失败**不等于写不了**。实测 Chrome 的 `entry "Name"`
            # （chrome://settings/manageProfile 里的用户名框）明明holds "Person 1"，
            # set_value 却报"不可设置"——Blink 的输入框不实现 AT-SPI 的
            # Value/EditableText 接口。
            #
            # click 和 type_text 早就有"语义失败就降级到合成"的模式，
            # set_value 却只会硬失败，于是一个到处都是的控件类型整个不可用。
            # 这里补上同一条降级：聚焦 → 全选 → 打字，与人手做的完全一样。
            #
            # 降级要**说清楚**：它没有回读确认，也不保证落到了那个控件上
            # ——键盘合成是全局的。
            require_window_focus(window, "set_value")
            focused = safe(lambda: Atspi.Component.grab_focus(
                element.get_component_iface()), False)
            if not focused:
                x, y = screen_point(bounds, element_record, None, None)
                send_mouse_click(x, y, "left", 1)
                time.sleep(0.1)
            send_key("ctrl+a")
            time.sleep(0.05)
            send_text(value)
            notes.append(
                addressing_channel(element_record)
                + SYNTHESIS
                + "The AT-SPI value API refused this element (Blink and some toolkits "
                "do not implement it on text inputs), so this fell back to focusing "
                "the control, selecting all, and typing. There is no read-back "
                "confirmation on this path, and keyboard synthesis is global — verify "
                "from the tree that the control now holds what you wanted. {}".format(
                    UNVERIFIED_SYNTHESIS)
            )
        else:
            notes.append(
                A11Y_CHANNEL
                + SEMANTIC
                + "Wrote the value through the AT-SPI API and read it back to confirm the "
                "control now holds it. Note the limit of that check: it confirms the "
                "CONTROL changed, not that the application adopted the value. Dialogs "
                "commonly keep control state separate from document state and only commit "
                "on OK/Apply — verify the actual effect (document content, a re-read of "
                "the relevant element) rather than trusting this line."
            )
    else:
        raise RuntimeError('unsupportedTool("{}")'.format(tool))

    settle_note = wait_for_ui_to_settle(app, windows_before)
    if settle_note:
        notes.append(settle_note)
    # 像素比对必须在**安置之后**：动作刚发出去时界面还没画完，
    # 那时比出来的"变化"是渲染中途的样子，不是效果。
    after_bounds = safe(lambda: extents(main_window(app)[1])) or bounds
    pixels_after_one = safe(lambda: capture_window_pixels(after_bounds))
    after_snapshot = build_snapshot(
        operation.get("app", ""),
        # 动作后的快照按当前策略带图；`SCREENSHOT_REQUIRED_TOOLS` 里的
        # 工具无视策略，因为它们的效果树里根本看不出来。
        include_screenshot=True if tool in SCREENSHOT_REQUIRED_TOOLS else None,
        known_refs=operation.get("knownRefs"),
    )
    # 动作**开出**模态对话框是最需要说这句话的时刻：下标全部重排，而 agent
    # 手上那份还是点击之前的。插在最前面，让它在所有动作 Note 之前被读到。
    notes[:0] = after_snapshot.pop("modalNotes", [])
    response = {"ok": True, "snapshot": after_snapshot}
    # 第二张动作后画面放在**建完快照之后**抓：resolve_app 自己就要 0.15–0.3s，
    # 这段已有的耗时正好当作两张之间的间隔，足够让 1Hz 的光标闪一次。
    change_note = pixel_change_note(persistent_pixel_change(
        pixels_before, pixels_after_one,
        safe(lambda: capture_window_pixels(after_bounds))))
    if change_note:
        notes.append(change_note)
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
