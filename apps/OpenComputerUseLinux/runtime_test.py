#!/usr/bin/env python3
"""runtime.py 的单元测试。

只依赖标准库 unittest，用假的 AT-SPI 节点驱动，不需要桌面会话或 a11y 总线，
因此可以直接进 CI。真实桌面上的端到端验证见
`scripts/verify-linux-input-chain.py`。

覆盖的是两类曾经静默失败的路径：
1. 选错可编辑控件 —— 写进隐藏占位控件，insert_text 返回 True 但文本丢失。
2. 全局输入合成 —— XTEST 落到当前焦点窗口，与 app 参数无关。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import runtime  # noqa: E402

STATE = runtime.Atspi.StateType


class FakeStateSet:
    def __init__(self, states):
        self._states = set(states)

    def contains(self, state):
        return state in self._states


class FakeSpan:
    def __init__(self, start, end):
        self.start_offset = start
        self.end_offset = end


class FakeTextIface:
    """假的 Text/EditableText 接口。

    `honest=False` 复现真实世界里的坑：insert_text 返回 True，字符数却不变。
    `caret=None` 模拟不实现 caret 查询的控件。
    """

    def __init__(self, text="", honest=True, accept=True, caret=None, selection=None):
        self.text = text
        self.honest = honest
        self.accept = accept
        self.caret = caret
        self.selection = selection
        self.inserts = []
        self.deletes = []
        self.set_calls = []

    def insert(self, offset, payload):
        self.inserts.append((offset, payload))
        if not self.accept:
            return False
        if self.honest:
            self.text = self.text[:offset] + payload + self.text[offset:]
        return True

    def delete(self, start, end):
        self.deletes.append((start, end))
        if self.honest:
            self.text = self.text[:start] + self.text[end:]
        return True

    def set_contents(self, payload):
        self.set_calls.append(payload)
        if not self.accept:
            return False
        if self.honest:
            self.text = payload
        return True


class FakeExtents:
    def __init__(self, x, y, width, height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class FakeComponent:
    def __init__(self, window=None, grabs=False, extents=None):
        self.window = window
        self.grabs = grabs
        self.extents = extents
        self.grab_calls = 0

    def grab_focus(self):
        self.grab_calls += 1
        if not self.grabs:
            return False
        if self.window is not None:
            self.window.states.add(STATE.ACTIVE)
        return True


class FakeNode:
    def __init__(
        self,
        states=(),
        text=None,
        editable=True,
        children=(),
        component=None,
        extents=None,
        value=None,
    ):
        self.states = set(states)
        self._text = text
        self._editable = editable
        self.children = list(children)
        self.component = component
        self.extents = extents
        self.value = value

    # --- AT-SPI Accessible surface used by runtime.py ---
    def get_state_set(self):
        return FakeStateSet(self.states)

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_text_iface(self):
        return self._text

    def get_editable_text_iface(self):
        return self._text if (self._text is not None and self._editable) else None

    def get_component_iface(self):
        return self.component

    def get_value_iface(self):
        return self.value

    def get_name(self):
        return ""

    def get_role_name(self):
        # 真实 AT-SPI 节点一律实现这个方法。假节点缺了它会让 node_role()
        # 在属性访问阶段就抛 AttributeError——safe() 收的是可调用对象，
        # 拦不住这一步。
        return ""


class FakeAtspiText:
    @staticmethod
    def get_character_count(iface):
        return len(iface.text)

    @staticmethod
    def get_text(iface, start, end):
        return iface.text[start:end]

    @staticmethod
    def get_caret_offset(iface):
        if iface.caret is None:
            raise RuntimeError("caret not supported")
        return iface.caret

    @staticmethod
    def get_n_selections(iface):
        return 1 if iface.selection else 0

    @staticmethod
    def get_selection(iface, index):
        start, end = iface.selection
        return FakeSpan(start, end)


class FakeAtspiEditableText:
    @staticmethod
    def insert_text(iface, offset, payload, length):
        return iface.insert(offset, payload)

    @staticmethod
    def delete_text(iface, start, end):
        return iface.delete(start, end)

    @staticmethod
    def set_text_contents(iface, payload):
        return iface.set_contents(payload)


class FakeAtspiComponent:
    @staticmethod
    def grab_focus(component):
        return component.grab_focus()

    @staticmethod
    def get_extents(component, coord_type):
        return component.extents


class FakeCoordType:
    SCREEN = "screen"


RELATION = runtime.Atspi.RelationType


class FakeAtspi:
    """保留真的 StateType 枚举，只替换会打到真实 a11y 总线的接口调用。"""

    StateType = STATE
    # RelationType 和 StateType 一样保留**真的**枚举：labelled_by_name() 拿它
    # 做相等比较，换成假值就等于测了一个不存在的分支。
    RelationType = RELATION
    CoordType = FakeCoordType
    Text = FakeAtspiText
    EditableText = FakeAtspiEditableText
    Component = FakeAtspiComponent


class AtspiPatchedTestCase(unittest.TestCase):
    def setUp(self):
        self._real_atspi = runtime.Atspi
        runtime.Atspi = FakeAtspi
        self.addCleanup(self._restore)

    def _restore(self):
        runtime.Atspi = self._real_atspi


class FindEditableTextTests(AtspiPatchedTestCase):
    def test_skips_node_that_lacks_editable_state(self):
        """回归：树序第一个带 EditableText 接口的节点可能是隐藏占位控件。

        它没有 EDITABLE 状态，写进去会静默丢失，必须跳过。
        """
        placeholder = FakeNode(
            states=(STATE.VISIBLE, STATE.ENABLED), text=FakeTextIface(honest=False)
        )
        real_editor = FakeNode(
            states=(STATE.FOCUSED, STATE.EDITABLE, STATE.SHOWING),
            text=FakeTextIface(),
        )
        window = FakeNode(children=(placeholder, real_editor))

        self.assertIs(runtime.find_editable_text(window), real_editor)

    def test_prefers_focused_over_merely_showing(self):
        showing = FakeNode(states=(STATE.EDITABLE, STATE.SHOWING), text=FakeTextIface())
        focused = FakeNode(
            states=(STATE.EDITABLE, STATE.SHOWING, STATE.FOCUSED), text=FakeTextIface()
        )
        window = FakeNode(children=(showing, focused))

        self.assertIs(runtime.find_editable_text(window), focused)

    def test_prefers_showing_over_offscreen(self):
        offscreen = FakeNode(states=(STATE.EDITABLE,), text=FakeTextIface())
        showing = FakeNode(states=(STATE.EDITABLE, STATE.SHOWING), text=FakeTextIface())
        window = FakeNode(children=(offscreen, showing))

        self.assertIs(runtime.find_editable_text(window), showing)

    def test_returns_none_when_no_node_has_editable_state(self):
        window = FakeNode(
            children=(
                FakeNode(states=(STATE.VISIBLE,), text=FakeTextIface()),
                FakeNode(states=(STATE.ENABLED,), text=FakeTextIface()),
            )
        )

        self.assertIsNone(runtime.find_editable_text(window))

    def test_ignores_nodes_without_editable_text_iface(self):
        read_only = FakeNode(
            states=(STATE.EDITABLE, STATE.FOCUSED),
            text=FakeTextIface(),
            editable=False,
        )
        editor = FakeNode(states=(STATE.EDITABLE,), text=FakeTextIface())
        window = FakeNode(children=(read_only, editor))

        self.assertIs(runtime.find_editable_text(window), editor)

    def test_finds_editor_nested_deep_in_the_tree(self):
        editor = FakeNode(
            states=(STATE.EDITABLE, STATE.FOCUSED, STATE.SHOWING), text=FakeTextIface()
        )
        window = FakeNode(
            children=(FakeNode(children=(FakeNode(children=(editor,)),)),)
        )

        self.assertIs(runtime.find_editable_text(window), editor)


class InsertTextTests(AtspiPatchedTestCase):
    def _window_with(self, iface):
        return FakeNode(
            children=(
                FakeNode(
                    states=(STATE.EDITABLE, STATE.FOCUSED, STATE.SHOWING), text=iface
                ),
            )
        )

    def test_rejects_buffer_that_reports_success_without_writing(self):
        """回归：AT-SPI 对错误控件也会返回 True，必须回读字符数确认。"""
        liar = FakeTextIface(honest=False)
        window = self._window_with(liar)

        self.assertFalse(runtime.insert_text(window, "hello"))
        self.assertEqual(liar.inserts, [(0, "hello")], "应确实尝试过写入")

    def test_accepts_buffer_that_actually_grows(self):
        iface = FakeTextIface()
        window = self._window_with(iface)

        self.assertTrue(runtime.insert_text(window, "hello"))
        self.assertEqual(iface.text, "hello")

    def test_appends_at_end_of_existing_content(self):
        iface = FakeTextIface(text="abc")
        window = self._window_with(iface)

        self.assertTrue(runtime.insert_text(window, "def"))
        self.assertEqual(iface.text, "abcdef")

    def test_returns_false_when_insert_itself_fails(self):
        iface = FakeTextIface(accept=False)
        window = self._window_with(iface)

        self.assertFalse(runtime.insert_text(window, "hello"))

    def test_empty_payload_is_a_successful_noop(self):
        iface = FakeTextIface(text="abc")
        window = self._window_with(iface)

        self.assertTrue(runtime.insert_text(window, ""))
        self.assertEqual(iface.inserts, [], "空文本不该产生写入调用")

    def test_returns_false_when_no_editable_target_exists(self):
        window = FakeNode(children=(FakeNode(states=(STATE.VISIBLE,)),))

        self.assertFalse(runtime.insert_text(window, "hello"))


class ExtentsTests(AtspiPatchedTestCase):
    def _node(self, x, y, width, height):
        return FakeNode(component=FakeComponent(extents=FakeExtents(x, y, width, height)))

    def test_accepts_a_normal_on_screen_widget(self):
        self.assertEqual(
            runtime.extents(self._node(10, 20, 100, 40)),
            runtime.frame(10, 20, 100, 40),
        )

    def test_accepts_negative_coordinates_from_a_second_monitor(self):
        """左侧副屏上的窗口坐标是负数，这是合法的，不能一并过滤掉。"""
        self.assertIsNotNone(runtime.extents(self._node(-1920, -100, 800, 600)))

    def test_rejects_int_min_origin_used_for_unrendered_widgets(self):
        """回归：未渲染控件返回 INT_MIN 原点但尺寸看着正常，只查尺寸拦不住。"""
        self.assertIsNone(runtime.extents(self._node(-2147483648, -2147483648, 1, 1)))

    def test_rejects_int_min_origin_on_either_axis(self):
        self.assertIsNone(runtime.extents(self._node(-2147483648, 20, 100, 40)))
        self.assertIsNone(runtime.extents(self._node(10, -2147483648, 100, 40)))

    def test_still_rejects_absurd_sizes(self):
        self.assertIsNone(runtime.extents(self._node(0, 0, 0, 40)))
        self.assertIsNone(runtime.extents(self._node(0, 0, 100, 999999)))


class TextInsertionPointTests(AtspiPatchedTestCase):
    def test_uses_caret_offset(self):
        iface = FakeTextIface(text="abcdef", caret=3)

        self.assertEqual(runtime.text_insertion_point(iface), (3, None))

    def test_prefers_a_non_empty_selection_over_the_caret(self):
        iface = FakeTextIface(text="abcdef", caret=0, selection=(2, 5))

        self.assertEqual(runtime.text_insertion_point(iface), (2, (2, 5)))

    def test_ignores_a_collapsed_selection(self):
        iface = FakeTextIface(text="abcdef", caret=4, selection=(2, 2))

        self.assertEqual(runtime.text_insertion_point(iface), (4, None))

    def test_falls_back_to_appending_when_caret_is_unavailable(self):
        iface = FakeTextIface(text="abcdef", caret=None)

        self.assertEqual(runtime.text_insertion_point(iface), (6, None))

    def test_falls_back_to_appending_when_caret_is_out_of_range(self):
        iface = FakeTextIface(text="abc", caret=99)

        self.assertEqual(runtime.text_insertion_point(iface), (3, None))


class CaretInsertionTests(AtspiPatchedTestCase):
    def _window_with(self, iface):
        return FakeNode(
            children=(
                FakeNode(
                    states=(STATE.EDITABLE, STATE.FOCUSED, STATE.SHOWING), text=iface
                ),
            )
        )

    def test_inserts_at_the_caret_instead_of_the_end(self):
        """回归：一直追加到末尾，等于无视用户/agent 刚刚放好的光标位置。"""
        iface = FakeTextIface(text="hello world", caret=5)

        self.assertTrue(runtime.insert_text(self._window_with(iface), " there"))
        self.assertEqual(iface.text, "hello there world")

    def test_replaces_a_selection_the_way_typing_does(self):
        iface = FakeTextIface(text="hello world", caret=0, selection=(0, 5))

        self.assertTrue(runtime.insert_text(self._window_with(iface), "goodbye"))
        self.assertEqual(iface.deletes, [(0, 5)])
        self.assertEqual(iface.text, "goodbye world")

    def test_appends_when_the_control_has_no_caret(self):
        iface = FakeTextIface(text="abc", caret=None)

        self.assertTrue(runtime.insert_text(self._window_with(iface), "def"))
        self.assertEqual(iface.text, "abcdef")

    def test_detail_reports_character_counts(self):
        iface = FakeTextIface(text="abc", caret=3)

        self.assertEqual(
            runtime.insert_text_detail(self._window_with(iface), "de"), (True, 3, 5)
        )

    def test_detail_reports_failure_without_growth(self):
        iface = FakeTextIface(text="abc", caret=3, honest=False)

        written, before, after = runtime.insert_text_detail(
            self._window_with(iface), "de"
        )
        self.assertFalse(written)
        self.assertEqual((before, after), (3, 3))


class SetElementValueTests(AtspiPatchedTestCase):
    def test_confirms_the_write_landed(self):
        iface = FakeTextIface(text="old")
        node = FakeNode(states=(STATE.EDITABLE,), text=iface)

        self.assertTrue(runtime.set_element_value(node, "new"))
        self.assertEqual(iface.text, "new")

    def test_rejects_a_control_that_reports_success_without_changing(self):
        """回归：set_text_contents 和 insert_text 一样会对错误控件返回 True。"""
        iface = FakeTextIface(text="old", honest=False)
        node = FakeNode(states=(STATE.EDITABLE,), text=iface)

        self.assertFalse(runtime.set_element_value(node, "new"))

    def test_accepts_a_control_that_normalizes_the_value(self):
        """有的控件会规范化输入，只要确实变了就算写进去了。"""
        iface = FakeTextIface(text="old")

        def normalize(payload):
            iface.set_calls.append(payload)
            iface.text = payload.strip().upper()
            return True

        iface.set_contents = normalize
        node = FakeNode(states=(STATE.EDITABLE,), text=iface)

        self.assertTrue(runtime.set_element_value(node, " new "))
        self.assertEqual(iface.text, "NEW")


class ChannelTaggingTests(AtspiPatchedTestCase):
    """回归：每条动作 note 必须带通道标签，否则"语义 vs 坐标"的比例无法统计。

    这个比例是 plan 里 S3 报告口径的第四项，也是区分"agent 不想用 a11y"
    与"用了但失败后退化"的唯一依据——两者修法相反。
    """

    def setUp(self):
        super().setUp()
        self.window = FakeNode(states=(STATE.ACTIVE,))
        for name, value in (
            ("resolve_app", lambda q: FakeNode()),
            ("main_window", lambda a: (0, self.window)),
            ("extents", lambda n: runtime.frame(0, 0, 100, 100)),
            ("find_element", lambda a, r: None),
            ("build_snapshot", lambda *a, **k: {"text": ""}),
            ("require_window_focus", lambda w, what: None),
            ("parse_key", lambda k: ([], k)),
            ("send_key", lambda k: None),
            ("send_text", lambda t: None),
            ("send_mouse_click", lambda *a: None),
            ("send_drag", lambda *a: None),
            # scroll_element 现在收第三个参数（元素坐标）并**返回走了哪条路线**，
            # Note 的措辞依赖这个返回值。桩子跟着改，否则测的是一个不存在的签名。
            ("scroll_element", lambda d, p, point=None: "keys"),
            ("time", _NoSleep()),
        ):
            original = getattr(runtime, name)
            setattr(runtime, name, value)
            self.addCleanup(setattr, runtime, name, original)

    def _notes(self, op):
        """只取动作 Note。

        `[pixels]` 那条是**独立于树**的效果判据，按动作类型无关地附加，
        不该混进"这个动作说了什么"的断言里。
        """
        return [n for n in runtime.perform_operation(op).get("notes", [])
                if not n.startswith("[pixels]")]

    def test_semantic_and_synthesis_are_distinguishable(self):
        original = runtime.insert_text_detail
        runtime.insert_text_detail = lambda root, text: (True, 0, 2)
        self.addCleanup(setattr, runtime, "insert_text_detail", original)

        semantic = self._notes({"tool": "type_text", "app": "x", "text": "hi"})
        self.assertIn("[semantic] ", semantic[0])

        runtime.insert_text_detail = lambda root, text: (False, 0, 0)
        synthesis = self._notes({"tool": "type_text", "app": "x", "text": "hi"})
        self.assertIn("[synthesis] ", synthesis[0])

    def test_every_action_path_carries_a_channel_tag(self):
        original = runtime.insert_text_detail
        runtime.insert_text_detail = lambda root, text: (False, 0, 0)
        self.addCleanup(setattr, runtime, "insert_text_detail", original)

        for op in (
            {"tool": "press_key", "app": "x", "key": "a"},
            {"tool": "type_text", "app": "x", "text": "hi"},
            {"tool": "scroll", "app": "x", "direction": "down", "pages": 1},
            {"tool": "drag_xy", "app": "x", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            {"tool": "click_xy", "app": "x", "x": 5, "y": 7},
        ):
            notes = self._notes(op)
            self.assertTrue(notes, "{} 没有产生 note".format(op["tool"]))
            # 两个正交的轴各要有一个标签：寻址通道 + 执行路径。
            # 少任何一个，agent 都无法判断"该拿什么去验证这次动作"。
            self.assertTrue(
                notes[0].startswith(("[a11y]", "[gui]", "[keyboard]")),
                "{} 的 note 缺少寻址通道标签: {}".format(op["tool"], notes[0][:70]),
            )
            self.assertTrue(
                "[semantic] " in notes[0] or "[synthesis] " in notes[0],
                "{} 的 note 缺少执行路径标签: {}".format(op["tool"], notes[0][:70]),
            )

    def test_coordinate_click_reports_what_it_hit(self):
        """裸坐标点击原本是纯盲点：打完就走，说不出打到了什么。

        命中回报把它变成有反馈的动作。但必须**明说是提示不是证明**——
        实测 AT-SPI 命中测试在 VCL/Qt 上经常只解析到容器层
        （gedit 11/11，LibreOffice 12/25），拿它当真值等于用一个新的谎
        替换旧的沉默。
        """
        notes = self._notes({"tool": "click_xy", "app": "x", "x": 5, "y": 7})

        self.assertIn("[gui]", notes[0])
        self.assertIn("Hit test", notes[0])
        # 两个分支——命中了、没命中——都不许把话说满。
        self.assertTrue(
            "HINT, not" in notes[0] or "is unverified" in notes[0],
            "命中回报必须自陈不确定: {}".format(notes[0][-120:]),
        )


class ActionNotesTests(AtspiPatchedTestCase):
    """动作必须如实说明走了哪条路径、结果有没有被确认。"""

    def setUp(self):
        super().setUp()
        self.window = FakeNode(states=(STATE.ACTIVE,))
        self._patch("resolve_app", lambda query: FakeNode())
        self._patch("main_window", lambda app: (0, self.window))
        self._patch("extents", lambda node: runtime.frame(0, 0, 100, 100))
        self._patch("find_element", lambda app, record: None)
        self._patch("build_snapshot", lambda *a, **k: {"text": ""})
        self._patch("send_key", lambda key: None)
        self._patch("send_text", lambda text: None)
        self._patch("send_mouse_click", lambda *a: None)
        self._patch("time", _NoSleep())

    def _patch(self, name, value):
        original = getattr(runtime, name)
        setattr(runtime, name, value)
        self.addCleanup(setattr, runtime, name, original)

    def test_direct_write_is_reported_as_confirmed(self):
        self._patch("insert_text_detail", lambda root, text: (True, 3, 8))

        notes = [n for n in runtime.perform_operation(
            {"tool": "type_text", "app": "x", "text": "hello"}
        )["notes"] if not n.startswith("[pixels]")]

        self.assertEqual(len(notes), 1)
        self.assertIn("confirm it landed (3 -> 8 characters)", notes[0])
        self.assertNotIn("not verified", notes[0])
        # 必须说清确认的边界：确认的是控件变了，不是应用采纳了。
        # 对话框普遍把控件状态与文档状态分开，只在 OK/Apply 时才提交。
        self.assertIn("may still require OK/Apply", notes[0])

    def test_synthesis_fallback_is_reported_as_unverified(self):
        self._patch("insert_text_detail", lambda root, text: (False, 0, 0))

        notes = runtime.perform_operation(
            {"tool": "type_text", "app": "x", "text": "hello"}
        )["notes"]

        self.assertIn("fell back to", notes[0])
        self.assertIn("was not verified", notes[0])

    def test_press_key_is_always_reported_as_unverified(self):
        notes = runtime.perform_operation(
            {"tool": "press_key", "app": "x", "key": "ctrl+a"}
        )["notes"]

        self.assertIn("ctrl+a", notes[0])
        self.assertIn("was not verified", notes[0])

    def test_coordinate_click_is_reported_as_unverified(self):
        notes = runtime.perform_operation(
            {"tool": "click_xy", "app": "x", "x": 5, "y": 7}
        )["notes"]

        self.assertIn("coordinate click", notes[0])
        self.assertIn("was not verified", notes[0])

    def test_accessibility_click_is_reported_as_a_semantic_action(self):
        element = FakeNode()
        self._patch("find_element", lambda app, record: element)
        self._patch("preferred_action_index", lambda node: 0)
        self._patch("do_action_by_index", lambda node, index: True)

        notes = runtime.perform_operation(
            {"tool": "click", "app": "x", "click_method": "auto", "x": 1, "y": 1}
        )["notes"]

        self.assertIn("AT-SPI accessibility action", notes[0])
        self.assertNotIn("not verified", notes[0])

    def test_auto_click_reports_the_coordinate_fallback(self):
        element = FakeNode()
        self._patch("find_element", lambda app, record: element)
        self._patch("preferred_action_index", lambda node: None)
        self._patch("do_action_by_index", lambda node, index: False)

        notes = runtime.perform_operation(
            {"tool": "click", "app": "x", "click_method": "auto", "x": 1, "y": 1}
        )["notes"]

        self.assertIn("No usable AT-SPI action", notes[0])
        self.assertIn("was not verified", notes[0])


class SnapshotDiagnosticsTests(AtspiPatchedTestCase):
    """回归：应用活着、窗口也在，但 a11y 只有一个窗口框时必须明确告知。

    实测证据：Chrome 未加 --force-renderer-accessibility 时有 11 个活进程、
    AT-SPI 里有正常注册和窗口标题，但 get_app_state 返回 isError=false 且树里
    只有 1 个元素。agent 无从分辨"界面是空的"和"我看不见这个界面"。
    """

    def test_flags_app_that_exposes_only_its_window_frame(self):
        records = [
            {
                "controlType": "frame",
                "name": "about:blank - Google Chrome",
                "actions": ["doDefault", "showContextMenu"],
            }
        ]

        notes = runtime.snapshot_diagnostics(records)

        self.assertEqual(len(notes), 1)
        self.assertIn("exposes no accessibility content", notes[0])
        self.assertIn("force-renderer-accessibility", notes[0])
        self.assertIn("does NOT mean the window is empty", notes[0])

    def test_stays_silent_when_the_tree_has_real_content(self):
        records = [
            {"controlType": "frame", "name": "Untitled - gedit", "actions": []},
            {"controlType": "push button", "name": "Save", "actions": ["click"]},
        ]

        self.assertEqual(runtime.snapshot_diagnostics(records), [])

    def test_a_named_non_container_alone_counts_as_content(self):
        records = [
            {"controlType": "frame", "name": "Doc", "actions": []},
            {"controlType": "label", "name": "hello", "actions": []},
        ]

        self.assertEqual(runtime.snapshot_diagnostics(records), [])

    def test_containers_without_content_do_not_count(self):
        """只有一堆无名无动作的容器，等同于空壳。"""
        records = [
            {"controlType": "frame", "name": "App", "actions": ["doDefault"]},
            {"controlType": "panel", "name": "", "actions": []},
            {"controlType": "filler", "name": "", "actions": []},
        ]

        self.assertEqual(len(runtime.snapshot_diagnostics(records)), 1)

    def test_empty_record_set_is_not_diagnosed(self):
        """完全没有 records 是另一类问题（取不到窗口），不在本诊断范围内。"""
        self.assertEqual(runtime.snapshot_diagnostics([]), [])

    def test_get_app_state_returns_the_diagnostic_as_notes(self):
        shell = {
            "elements": [
                {"controlType": "frame", "name": "W", "actions": ["doDefault"]}
            ]
        }
        original = runtime.build_snapshot
        runtime.build_snapshot = lambda *a, **k: shell
        self.addCleanup(setattr, runtime, "build_snapshot", original)

        response = runtime.perform_operation({"tool": "get_app_state", "app": "x"})

        self.assertTrue(response["ok"])
        self.assertEqual(len(response["notes"]), 1)
        self.assertIn("exposes no accessibility content", response["notes"][0])


class MainWindowSelectionTests(AtspiPatchedTestCase):
    """回归：弹出模态对话框后，agent 必须能看到对话框而不是主窗口。

    实测证据：LibreOffice Writer 打开「格式 → 段落」对话框后，frame 与 dialog
    的状态分别是 (SHOWING, VISIBLE) 和 (SHOWING, VISIBLE, MODAL)——**两者都不报
    ACTIVE**。旧逻辑按 ACTIVE > SHOWING > 第一个 排序，于是模态对话框因为在
    子节点顺序里靠后而输给主窗口，`get_app_state` 返回主窗口的树，
    树里连一个 dialog 角色的节点都没有。而对话框是 OSWorld 的主要操作对象。
    """

    def _app(self, *windows):
        return FakeNode(children=windows)

    def test_visible_modal_dialog_wins_over_main_frame(self):
        frame = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE))
        dialog = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE, STATE.MODAL))
        self._patch_roles({id(frame): "frame", id(dialog): "dialog"})

        index, chosen = runtime.main_window(self._app(frame, dialog))

        self.assertIs(chosen, dialog)
        self.assertEqual(index, 1)

    def test_modal_dialog_wins_even_when_frame_claims_active(self):
        """模态按定义阻塞其余窗口，比 ACTIVE 更强。"""
        frame = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE, STATE.ACTIVE))
        dialog = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE, STATE.MODAL))
        self._patch_roles({id(frame): "frame", id(dialog): "dialog"})

        _, chosen = runtime.main_window(self._app(frame, dialog))

        self.assertIs(chosen, dialog)

    def test_hidden_modal_dialog_does_not_win(self):
        """已关闭但仍挂在树上的模态对话框不该抢走主窗口。"""
        frame = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE, STATE.ACTIVE))
        stale = FakeNode(states=(STATE.MODAL,))
        self._patch_roles({id(frame): "frame", id(stale): "dialog"})

        _, chosen = runtime.main_window(self._app(frame, stale))

        self.assertIs(chosen, frame)

    def test_active_modal_wins_over_earlier_modal(self):
        """回归：combo 下拉是叠在对话框之上的第二个模态窗口。

        实测 LibreOffice：Paragraph 对话框是 MODAL+SHOWING，
        行距下拉弹出的独立顶层 window 是 MODAL+SHOWING+**ACTIVE**。
        只取"第一个模态"会拿到对话框，下拉里的选项仍然看不见。
        """
        dialog = FakeNode(states=(STATE.SHOWING, STATE.VISIBLE, STATE.MODAL))
        popup = FakeNode(
            states=(STATE.SHOWING, STATE.VISIBLE, STATE.MODAL, STATE.ACTIVE)
        )
        self._patch_roles({id(dialog): "dialog", id(popup): "window"})

        _, chosen = runtime.main_window(self._app(dialog, popup))

        self.assertIs(chosen, popup)

    def test_falls_back_to_active_then_showing(self):
        plain = FakeNode(states=(STATE.SHOWING,))
        active = FakeNode(states=(STATE.SHOWING, STATE.ACTIVE))
        self._patch_roles({id(plain): "frame", id(active): "frame"})

        _, chosen = runtime.main_window(self._app(plain, active))

        self.assertIs(chosen, active)

    def test_raises_when_no_window_exists(self):
        self._patch_roles({})
        with self.assertRaises(RuntimeError):
            runtime.main_window(FakeNode(children=()))

    def _patch_roles(self, mapping):
        original = runtime.node_role
        self.addCleanup(setattr, runtime, "node_role", original)
        runtime.node_role = lambda node: mapping.get(id(node), "frame")
        # extents 走真实 Atspi.Component，这里的 Fake 没有 component，
        # app_windows 靠 role 判定即可
        original_ext = runtime.extents
        self.addCleanup(setattr, runtime, "extents", original_ext)
        runtime.extents = lambda node: None


class FocusWindowTests(AtspiPatchedTestCase):
    def test_active_window_needs_no_grab(self):
        component = FakeComponent(grabs=True)
        window = FakeNode(
            states=(STATE.ACTIVE,),
            children=(FakeNode(states=(STATE.FOCUSABLE,), component=component),),
        )

        self.assertTrue(runtime.focus_window(window))
        self.assertEqual(component.grab_calls, 0, "已激活的窗口不该再抓焦点")

    def test_grabs_focusable_child_to_activate_window(self):
        window = FakeNode()
        component = FakeComponent(window=window, grabs=True)
        window.children = [FakeNode(states=(STATE.FOCUSABLE,), component=component)]

        self.assertTrue(runtime.focus_window(window))
        self.assertEqual(component.grab_calls, 1)

    def test_prefers_the_previously_focused_widget(self):
        """窗口失活后，上次获得焦点的控件仍保留 FOCUSED 状态，应优先试它。"""
        window = FakeNode()
        plain = FakeComponent(window=window, grabs=True)
        remembered = FakeComponent(window=window, grabs=True)
        window.children = [
            FakeNode(states=(STATE.FOCUSABLE,), component=plain),
            FakeNode(states=(STATE.FOCUSABLE, STATE.FOCUSED), component=remembered),
        ]

        self.assertTrue(runtime.focus_window(window))
        self.assertEqual(remembered.grab_calls, 1)
        self.assertEqual(plain.grab_calls, 0)

    def test_returns_false_when_nothing_can_take_focus(self):
        component = FakeComponent(grabs=False)
        window = FakeNode(
            children=(FakeNode(states=(STATE.FOCUSABLE,), component=component),)
        )

        self.assertFalse(runtime.focus_window(window))

    def test_skips_non_focusable_nodes(self):
        component = FakeComponent(grabs=True)
        window = FakeNode(children=(FakeNode(states=(STATE.VISIBLE,), component=component),))

        self.assertFalse(runtime.focus_window(window))
        self.assertEqual(component.grab_calls, 0)

    def test_caps_the_number_of_grab_attempts(self):
        """抓焦点会真的改变应用内焦点位置，不能把整棵树扫一遍。"""
        components = [FakeComponent(grabs=False) for _ in range(20)]
        window = FakeNode(
            children=[
                FakeNode(states=(STATE.FOCUSABLE,), component=c) for c in components
            ]
        )

        self.assertFalse(runtime.focus_window(window))
        attempted = sum(1 for c in components if c.grab_calls)
        self.assertEqual(attempted, runtime.FOCUS_GRAB_CANDIDATES)

    def test_none_window_is_not_focusable(self):
        self.assertFalse(runtime.focus_window(None))


class RequireWindowFocusTests(AtspiPatchedTestCase):
    def test_passes_through_when_window_is_active(self):
        window = FakeNode(states=(STATE.ACTIVE,))

        runtime.require_window_focus(window, "press_key")  # 不应抛异常

    def test_raises_instead_of_synthesizing_into_the_wrong_window(self):
        component = FakeComponent(grabs=False)
        window = FakeNode(
            children=(FakeNode(states=(STATE.FOCUSABLE,), component=component),)
        )

        with self.assertRaises(RuntimeError) as ctx:
            runtime.require_window_focus(window, "press_key")
        message = str(ctx.exception)
        self.assertIn("press_key", message)
        self.assertIn("foreground", message)


class PerformOperationGuardTests(AtspiPatchedTestCase):
    """确认焦点守卫真的接在了调度路径上，防止以后被摘掉。"""

    def setUp(self):
        super().setUp()
        self.window = FakeNode()
        self.focus_calls = []
        self.sent_keys = []
        self.sent_text = []
        self.clicks = []
        self.drags = []

        def fake_require(window, what):
            self.focus_calls.append(what)

        self._patch("resolve_app", lambda query: FakeNode())
        self._patch("main_window", lambda app: (0, self.window))
        self._patch("extents", lambda node: runtime.frame(0, 0, 100, 100))
        self._patch("find_element", lambda app, record: None)
        self._patch("build_snapshot", lambda *a, **k: {"text": ""})
        self._patch("require_window_focus", fake_require)
        self._patch("insert_text_detail", lambda root, text: (False, 0, 0))
        self._patch("send_text", lambda text: self.sent_text.append(text))
        self._patch("parse_key", lambda key: ([], key))
        self._patch("send_key", lambda key: self.sent_keys.append(key))
        self._patch(
            "send_mouse_click", lambda x, y, b, c: self.clicks.append((x, y, b, c))
        )
        self._patch("send_drag", lambda *a: self.drags.append(a))
        self._patch("time", _NoSleep())

    def _patch(self, name, value):
        original = getattr(runtime, name)
        setattr(runtime, name, value)
        self.addCleanup(setattr, runtime, name, original)

    def test_press_key_requires_focus_first(self):
        runtime.perform_operation({"tool": "press_key", "app": "x", "key": "ctrl+a"})

        self.assertEqual(self.focus_calls, ["press_key"])
        self.assertEqual(self.sent_keys, ["ctrl+a"])

    def test_invalid_key_is_rejected_before_stealing_focus(self):
        """夺焦点会打断用户，不该为一个拼错的按键先抢窗口再报错。"""

        def refuse_parse(key):
            raise RuntimeError("Unsupported modifier: shft")

        self._patch("parse_key", refuse_parse)

        with self.assertRaises(RuntimeError) as ctx:
            runtime.perform_operation({"tool": "press_key", "app": "x", "key": "shft+s"})
        self.assertIn("Unsupported modifier", str(ctx.exception))
        self.assertEqual(self.focus_calls, [], "参数非法时不该动用户的窗口")
        self.assertEqual(self.sent_keys, [])

    def test_type_text_requires_focus_before_synthesis_fallback(self):
        runtime.perform_operation({"tool": "type_text", "app": "x", "text": "hi"})

        self.assertEqual(self.focus_calls, ["type_text"])
        self.assertEqual(self.sent_text, ["hi"])

    def test_type_text_skips_focus_when_direct_write_succeeds(self):
        """AT-SPI 直写不需要焦点，不该无谓地抢用户的窗口。"""
        self._patch("insert_text_detail", lambda root, text: (True, 0, 2))

        runtime.perform_operation({"tool": "type_text", "app": "x", "text": "hi"})

        self.assertEqual(self.focus_calls, [])
        self.assertEqual(self.sent_text, [])

    def test_global_click_requires_focus(self):
        runtime.perform_operation(
            {"tool": "click", "app": "x", "click_method": "global", "x": 5, "y": 5}
        )

        self.assertEqual(self.focus_calls, ["click"])
        self.assertEqual(len(self.clicks), 1)

    def test_drag_requires_focus(self):
        runtime.perform_operation(
            {
                "tool": "drag_xy",
                "app": "x",
                "from_x": 1,
                "from_y": 2,
                "to_x": 3,
                "to_y": 4,
            }
        )

        self.assertEqual(self.focus_calls, ["drag_xy"])
        self.assertEqual(len(self.drags), 1)

    def test_scroll_requires_focus(self):
        runtime.perform_operation(
            {"tool": "scroll", "app": "x", "direction": "down", "pages": 1}
        )

        self.assertEqual(self.focus_calls, ["scroll"])

    def test_failed_focus_aborts_before_any_synthesis(self):
        def refuse(window, what):
            raise RuntimeError("Refusing to synthesize {}".format(what))

        self._patch("require_window_focus", refuse)

        with self.assertRaises(RuntimeError):
            runtime.perform_operation({"tool": "press_key", "app": "x", "key": "a"})
        self.assertEqual(self.sent_keys, [], "夺焦点失败后不得再合成任何按键")


class _LyingNode(FakeNode):
    """自报子节点数与真实子节点数不一致的节点。

    这不是构造出来的假设：Calc 的 sheet 自报 21 亿个子节点（accessible range
    是整张表），Nautilus 的侧边栏声明自管理却只有 12 个。守卫必须只看
    **自报数**，因为真实数量恰恰是它不该去问的东西。
    """

    def __init__(self, reported, **kwargs):
        super().__init__(**kwargs)
        self.reported = reported

    def get_child_count(self):
        return self.reported


class ShouldEnumerateChildrenTests(AtspiPatchedTestCase):
    def test_nautilus_style_small_managed_container_is_enumerated(self):
        """回归：声明 MANAGES_DESCENDANTS 但很小的容器必须照常枚举。

        Nautilus 侧边栏（Recent/Home/Documents/…）声明了自管理，实际只有 12
        个子节点，而且**不实现 Table 接口**——坐标寻址兜底必然失败。一律拒绝
        枚举的旧实现让整个侧边栏对 agent 不可见：树里只留下一句
        "contents not enumerated"，导航功能等于不存在。
        """
        sidebar = _LyingNode(12, states=(STATE.MANAGES_DESCENDANTS, STATE.SHOWING))

        self.assertTrue(runtime.should_enumerate_children(sidebar))

    def test_calc_style_sheet_is_still_refused(self):
        """自报 21 亿的 Calc sheet 仍然不得枚举——放宽自管理不能把它放进来。

        它被 HARD_CHILD_CAP 拦下，与 MANAGES_DESCENDANTS 无关，所以即使
        自管理这条分支整个改掉，这道守卫也依然成立。
        """
        sheet = _LyingNode(2147483647, states=(STATE.MANAGES_DESCENDANTS, STATE.SHOWING))

        self.assertFalse(runtime.should_enumerate_children(sheet))

    def test_managed_container_above_cap_is_refused(self):
        """自管理且自报数量超过阈值时，回到"按契约不枚举"。"""
        big = _LyingNode(
            runtime.MANAGED_ENUMERATE_CAP + 1, states=(STATE.MANAGES_DESCENDANTS,)
        )

        self.assertFalse(runtime.should_enumerate_children(big))

    def test_managed_container_exactly_at_cap_is_enumerated(self):
        """阈值取闭区间：恰好等于上限仍然枚举，避免边界上少一个元素。"""
        edge = _LyingNode(
            runtime.MANAGED_ENUMERATE_CAP, states=(STATE.MANAGES_DESCENDANTS,)
        )

        self.assertTrue(runtime.should_enumerate_children(edge))

    def test_huge_container_without_managed_state_is_refused(self):
        """不声明自管理却谎报海量子节点的实现，同样要被硬上限兜住。"""
        liar = _LyingNode(runtime.HARD_CHILD_CAP + 1, states=(STATE.SHOWING,))

        self.assertFalse(runtime.should_enumerate_children(liar))

    def test_ordinary_container_is_enumerated(self):
        ordinary = FakeNode(children=(FakeNode(), FakeNode()))

        self.assertTrue(runtime.should_enumerate_children(ordinary))


class _TreeNode(FakeNode):
    """够渲染一棵树用的节点。FakeNode 只覆盖了 a11y 接口取值那部分，
    record_for() 还要 role / toolkit / accessible_id / attributes 等一整圈。"""

    def __init__(self, role="panel", name="", x=0, y=0, w=10, h=10, kids=(),
                 description=""):
        super().__init__(
            states=(STATE.SHOWING, STATE.VISIBLE),
            children=kids,
            component=FakeComponent(extents=FakeExtents(x, y, w, h)),
        )
        self.role = role
        self.title = name
        self.description = description

    def get_role_name(self):
        return self.role

    def get_name(self):
        return self.title

    def get_description(self):
        return self.description

    def get_toolkit_name(self):
        return "gtk"

    def get_accessible_id(self):
        return ""

    def get_action_iface(self):
        return None

    def get_n_actions(self):
        return 0

    def get_table_iface(self):
        return None

    def get_attributes(self):
        return {}

    def get_process_id(self):
        return 1


class RenderIndentationTests(AtspiPatchedTestCase):
    def _indent(self, line):
        return len(line) - len(line.lstrip("\t"))

    def _nodes(self, lines):
        """只取节点行。末尾可能跟一句"N node(s) omitted"提示，它不是节点。"""
        return [l for l in lines if l.strip() and l.strip()[0].isdigit()]

    def test_pruned_container_does_not_advance_child_indentation(self):
        """回归：被裁掉的中间容器不得推进子节点的缩进。

        裁剪只丢容器自己那一行、仍然递归子节点。早先子节点沿用遍历深度做缩进，
        于是每被裁掉一层，缩进就凭空多一格——实测 Nautilus 上从第 1 层直接
        跳到第 6 层，中间全是空档，读起来像一棵断掉的树，agent 无法据此判断
        父子关系（"这个按钮属于哪个面板"正是消歧无名控件的唯一线索）。
        """
        # filler 无名、无动作 → 会被裁剪；button 有名字 → 会被保留
        leaf = _TreeNode(role="push button", name="Save", x=1, y=1, w=5, h=5)
        middle = _TreeNode(role="filler", x=0, y=0, w=8, h=8, kids=(leaf,))
        root = _TreeNode(role="frame", name="Doc", w=100, h=100, kids=(middle,))

        _, lines = runtime.render_tree(root, None, [0], prune=True)

        rendered = self._nodes(lines)
        self.assertEqual(len(rendered), 2, "中间的 filler 应被裁掉：{}".format(rendered))
        self.assertIn('frame "Doc"', rendered[0])
        self.assertIn('push button "Save"', rendered[1])
        self.assertEqual(
            self._indent(rendered[1]) - self._indent(rendered[0]), 1,
            "被裁的 filler 不该在缩进上留下空档：{}".format(rendered),
        )

    def test_unpruned_tree_keeps_one_level_per_node(self):
        """不裁剪时缩进仍与真实层级一一对应，别为了修裁剪把正常路径改坏。"""
        leaf = _TreeNode(role="push button", name="Save", x=1, y=1, w=5, h=5)
        middle = _TreeNode(role="filler", x=0, y=0, w=8, h=8, kids=(leaf,))
        root = _TreeNode(role="frame", name="Doc", w=100, h=100, kids=(middle,))

        _, lines = runtime.render_tree(root, None, [0], prune=False)

        rendered = self._nodes(lines)
        self.assertEqual(len(rendered), 3)
        self.assertEqual(
            [self._indent(l) - self._indent(rendered[0]) for l in rendered], [0, 1, 2]
        )


class NodeDescriptionTests(AtspiPatchedTestCase):
    def _nodes(self, lines):
        return [l for l in lines if l.strip() and l.strip()[0].isdigit()]

    def test_unnamed_button_is_identified_by_its_description(self):
        """回归：GTK 的纯图标按钮名字为空，唯一可读标识在 description 里。

        实测 Nautilus 工具栏：`Go back` / `Go forward` / `Search` / `Show list`
        四个按钮 name 全空。不渲染 description 的话，返回/前进这类文件管理器
        核心操作在树里就只是一个无名 `push button`，agent 除了按像素坐标猜
        没有别的办法——a11y 优先的路径在这里直接断掉。
        """
        back = _TreeNode(role="push button", description="Go back", x=1, y=1, w=5, h=5)
        root = _TreeNode(role="frame", name="Files", w=100, h=100, kids=(back,))

        _, lines = runtime.render_tree(root, None, [0], prune=True)

        self.assertIn('[desc="Go back"]', "\n".join(lines))

    def test_description_disambiguates_identically_named_buttons(self):
        """三个都叫 Menu 的 toggle button 只能靠 description 区分。"""
        ops = _TreeNode(role="toggle button", name="Menu",
                        description="Show operations", x=1, y=1, w=5, h=5)
        view = _TreeNode(role="toggle button", name="Menu",
                         description="View options", x=7, y=1, w=5, h=5)
        root = _TreeNode(role="frame", name="Files", w=100, h=100, kids=(ops, view))

        _, lines = runtime.render_tree(root, None, [0], prune=True)

        rendered = self._nodes(lines)
        self.assertIn("Show operations", rendered[1])
        self.assertIn("View options", rendered[2])

    def test_description_equal_to_name_is_not_repeated(self):
        """description 与 name 相同时不重复渲染，白占 token。"""
        node = _TreeNode(role="push button", name="Home", description="Home",
                         x=1, y=1, w=5, h=5)
        root = _TreeNode(role="frame", name="Files", w=100, h=100, kids=(node,))

        _, lines = runtime.render_tree(root, None, [0], prune=True)

        self.assertNotIn("Description:", "\n".join(lines))

    def test_name_is_not_overwritten_by_description(self):
        """description 不得顶替 name：轨迹回放与保留率评测都按 role+name 匹配，
        改写 name 会让同一个元素在不同版本间对不上号。"""
        back = _TreeNode(role="push button", description="Go back", x=1, y=1, w=5, h=5)
        root = _TreeNode(role="frame", name="Files", w=100, h=100, kids=(back,))

        records, _ = runtime.render_tree(root, None, [0], prune=True)

        self.assertEqual(records[1]["name"], "")
        self.assertEqual(records[1]["description"], "Go back")


class StaleElementIndexTests(AtspiPatchedTestCase):
    """`runtimeId` 是位置性路径，树一变就指向别的控件。"""

    def record(self, role, name, frame=None, path=(0, 1)):
        return {"runtimeId": list(path), "controlType": role, "name": name,
                "automationId": "", "frame": frame}

    def test_rejects_node_whose_role_and_name_changed(self):
        """回归：陈旧下标解析到的是另一个控件时必须拒绝，不能照点不误。

        实测 Nautilus：拿右键菜单打开时的快照（`menu item Rename…`），菜单关掉后
        同一条路径指向工具栏的 `toggle button Menu`——"重命名"变成"切换视图选项"，
        全程 isError=False。同一份菜单里紧挨着就是 `Move to Trash`，
        静默点错是不可接受的失败模式。
        """
        other = _TreeNode(role="toggle button", name="Menu", x=715, y=23, w=26, h=46)

        self.assertFalse(
            runtime.record_still_matches(
                other, self.record("menu item", "Rename…"), None
            )
        )

    def test_accepts_unchanged_node(self):
        same = _TreeNode(role="menu item", name="Rename…", x=7, y=189, w=304, h=25)

        self.assertTrue(
            runtime.record_still_matches(
                same, self.record("menu item", "Rename…"), None
            )
        )

    def test_unnamed_element_is_matched_by_position(self):
        """对话框里大量控件没有名字，位置是唯一可用的身份线索。"""
        node = _TreeNode(role="toggle button", x=10, y=20, w=30, h=40)
        frame = {"x": 10, "y": 20, "width": 30, "height": 40}
        moved = {"x": 400, "y": 20, "width": 30, "height": 40}

        self.assertTrue(
            runtime.record_still_matches(node, self.record("toggle button", "", frame), None)
        )
        self.assertFalse(
            runtime.record_still_matches(node, self.record("toggle button", "", moved), None)
        )

    def test_named_node_does_not_satisfy_unnamed_record(self):
        """快照里没名字、解析出来的有名字，说明换了元素。"""
        named = _TreeNode(role="toggle button", name="Menu", x=10, y=20, w=30, h=40)
        frame = {"x": 10, "y": 20, "width": 30, "height": 40}

        self.assertFalse(
            runtime.record_still_matches(named, self.record("toggle button", "", frame), None)
        )

    def test_accessible_id_wins_when_present(self):
        """工具包给了稳定 id 时以它为准，名字或位置变化都不影响。"""

        class WithID(_TreeNode):
            def get_accessible_id(self):
                return "save-button"

        node = WithID(role="push button", name="Save As", x=99, y=99, w=1, h=1)
        record = {"runtimeId": [0], "controlType": "push button", "name": "Save",
                  "automationId": "save-button", "frame": None}

        self.assertTrue(runtime.record_still_matches(node, record, None))


    def test_find_element_refuses_stale_path_end_to_end(self):
        """行为级回归：陈旧下标穿过 find_element 时不得解析成另一个控件。

        这是上面那些判据真正要防住的东西——单测判据函数只能证明判据本身对，
        证明不了调用方用上了它。
        """
        toolbar = _TreeNode(role="toggle button", name="Menu", x=715, y=23, w=26, h=46)
        window = _TreeNode(role="frame", name="Files", w=900, h=600, kids=(toolbar,))
        app = _TreeNode(role="application", name="Files", kids=(window,))

        stale = {"runtimeId": [0, 0], "controlType": "menu item",
                 "name": "Rename…", "automationId": "", "frame": None}

        # 路径本身解析得到 toolbar，但它已经不是当初那个元素
        self.assertIs(runtime.resolve_path(app, [0, 0]), toolbar)
        self.assertIsNone(runtime.find_element(app, stale))

    def test_find_element_still_resolves_valid_path(self):
        item = _TreeNode(role="menu item", name="Rename…", x=7, y=189, w=304, h=25)
        window = _TreeNode(role="frame", name="Files", w=900, h=600, kids=(item,))
        app = _TreeNode(role="application", name="Files", kids=(window,))

        record = {"runtimeId": [0, 0], "controlType": "menu item",
                  "name": "Rename…", "automationId": "", "frame": None}

        self.assertIs(runtime.find_element(app, record), item)


class ClickableMarkerTests(AtspiPatchedTestCase):
    """agent 必须能从树里看出哪个元素可以按元素点、哪个只能坐标点。"""

    def make(self, actions):
        class Node(_TreeNode):
            def get_action_iface(self):
                # 真实节点没有动作时不实现 Action 接口。运行时先问接口再问动作数，
                # 否则 LibreOffice 的 ATK 桥会对非 ATK_ACTION 对象打断言。
                return object() if actions else None

            def get_n_actions(self):
                return len(actions)

            def get_action_name(self, index):
                return actions[index]

            def get_action_description(self, index):
                return ""

        return Node(role="push button", name="Home", x=1, y=1, w=5, h=5)

    def segment(self, node):
        """按 record_for 的真实用法：动作表只读一次，标记由它得出。"""
        _, has_click = runtime.node_actions(node)
        return runtime.state_segment(node, has_click_action=has_click)

    def test_element_with_click_action_is_marked(self):
        self.assertIn("has-click-action", self.segment(self.make(["click"])))

    def test_element_without_any_action_is_not_marked(self):
        """回归：Nautilus 侧边栏条目只有 component 接口，没有 Action 接口。

        旧渲染里它和一个有 click 动作的 push button 长得**一模一样**——
        action_names() 会隐藏 click 类动作，于是"有语义点击"和"根本点不了"
        被抹成同一个样子，agent 只好一律退回坐标，恰好背离 a11y 优先。
        """
        self.assertNotIn("has-click-action", self.segment(self.make([])))

    def test_marker_matches_what_click_would_actually_invoke(self):
        """标记与 click 真正调用的入口必须同源。

        两者现在是两份实现——`node_actions()` 出标记、`preferred_action_index()`
        出调用——因为渲染时的动作表只允许读一次（LibreOffice 的 ATK 桥被反复
        问会让应用整个退出）。既然实现分开了，就必须显式钉住它们的一致性，
        否则标记会变成新的谎言。
        """
        cases = [
            ["menu"],                    # 只有二级动作，click 无从下手
            ["activate"],                # 精确命中
            ["click", "menu"],           # 混合
            [],                          # 完全没有动作
            ["Press the button"],        # 只有描述式命名，靠子串兜底
        ]
        for actions in cases:
            node = self.make(actions)
            _, has_click = runtime.node_actions(node)
            invocable = runtime.preferred_action_index(node) is not None
            self.assertEqual(
                has_click, invocable,
                "动作表 {!r}：标记说 {}，实际可调用 {}".format(
                    actions, has_click, invocable),
            )
            self.assertEqual(has_click, "has-click-action" in self.segment(node))


class ResolveAppRetryTests(AtspiPatchedTestCase):
    """"暂时读不到"不等于"不存在"。"""

    def setUp(self):
        super().setUp()
        self.slept = []
        real_sleep = runtime.time.sleep
        runtime.time.sleep = lambda s: self.slept.append(s)
        self.addCleanup(setattr, runtime.time, "sleep", real_sleep)

    def patch_iter_apps(self, results):
        """results 是每次调用的返回值；元素为异常时抛出。"""
        calls = {"n": 0}

        def fake():
            index = min(calls["n"], len(results) - 1)
            calls["n"] += 1
            outcome = results[index]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        original = runtime.iter_apps
        runtime.iter_apps = fake
        self.addCleanup(setattr, runtime, "iter_apps", original)
        return calls

    def test_transient_empty_enumeration_is_retried(self):
        """回归：应用忙于重建窗口时枚举可能瞬时为空，不能就此宣告不存在。

        实测 LibreOffice：get_app_state 刚成功，紧接着 click 就报
        appNotFound("soffice")，而 AT-SPI 桌面里那一条自始至终都在。
        向 agent 谎报"应用不存在"会让它改用别的应用名、重启应用，
        甚至判定任务无法完成——而真相只是需要再读一次。
        """
        app = _TreeNode(role="application", name="soffice")
        self.patch_iter_apps([[], [], [app]])

        self.assertIs(runtime.resolve_app("soffice"), app)

    def test_enumeration_exception_is_retried(self):
        app = _TreeNode(role="application", name="soffice")
        self.patch_iter_apps([RuntimeError("dbus hiccup"), [app]])

        self.assertIs(runtime.resolve_app("soffice"), app)

    def test_genuinely_absent_app_still_fails(self):
        self.patch_iter_apps([[]])

        with self.assertRaises(RuntimeError) as caught:
            runtime.resolve_app("no-such-app")
        self.assertIn("appNotFound", str(caught.exception))

    def test_found_on_first_try_does_not_sleep(self):
        """应用在就立刻返回，不给正常路径加延迟。"""
        app = _TreeNode(role="application", name="soffice")
        self.patch_iter_apps([[app]])

        runtime.resolve_app("soffice")

        self.assertEqual(self.slept, [])


class DropdownItemTests(AtspiPatchedTestCase):
    """下拉项上的语义调用会关掉弹窗却不提交值，且事后无从校验。"""

    def popup(self):
        return _TreeNode(role="window", name="")

    def test_table_cell_in_unnamed_popup_is_a_dropdown_item(self):
        """回归：LibreOffice 行距下拉，点 `table cell Double`。

        实测 do_action 返回 True、下拉关闭、控件仍显示 Single（截图核实）；
        换坐标点击才真正提交。比"不生效"更糟的是事后无法校验——弹窗连同元素
        一起消失，动作前后的树必然不同，通用的"什么都没变就重试"不会触发；
        而对话框里的 combo box 是不上报值的幻影节点，读回来确认也读不到。
        """
        cell = _TreeNode(role="table cell", name="Double", x=3, y=71, w=358, h=21)

        self.assertTrue(runtime.is_dropdown_item(cell, self.popup()))

    def test_menu_item_is_not_treated_as_dropdown_item(self):
        """菜单项不在此列：Nautilus 右键菜单的 `Rename…` 实测 do_action 完全正常，
        一并改掉会把好路也堵死。"""
        item = _TreeNode(role="menu item", name="Rename…", x=7, y=189, w=304, h=25)

        self.assertFalse(runtime.is_dropdown_item(item, self.popup()))

    def test_table_cell_in_main_frame_is_not_a_dropdown_item(self):
        """Calc 的单元格用的就是 table cell 这个角色，它们不是下拉项。"""
        cell = _TreeNode(role="table cell", name="A1", x=10, y=10, w=80, h=20)
        frame = _TreeNode(role="frame", name="untitled - Calc", w=900, h=600)

        self.assertFalse(runtime.is_dropdown_item(cell, frame))

    def test_table_cell_in_named_dialog_is_not_a_dropdown_item(self):
        """对话框有标题、角色是 dialog；下拉是无名的 window。"""
        cell = _TreeNode(role="table cell", name="calc-test.csv", x=41, y=157, w=332, h=21)
        dialog = _TreeNode(role="dialog", name="Document Recovery", w=578, h=406)

        self.assertFalse(runtime.is_dropdown_item(cell, dialog))


class EmptyCellOmissionTests(AtspiPatchedTestCase):
    """Calc 视口里绝大多数是空单元格，全渲染等于把配额烧在没有信息的格子上。"""

    def test_notice_states_the_verified_path_only(self):
        """回归：给 agent 的替代路径必须是实测过的那一条。

        空单元格不进树之后就没有 element_index 了，提示必须说清楚怎么够到它们。
        最初写的是"用名称框输入引用再回车"——看起来更直接，但实测**没通过**：
        `set_value` 能改名称框的文本却不触发跳转（控件变了、应用没照做，
        与下拉提交同一族），`click` 也没能让它获得键盘焦点。
        实测能走通的是"press_key 移动单元格光标 + type_text"。
        """
        source = open(runtime.__file__, encoding="utf-8").read()

        self.assertIn("empty cell(s) omitted", source)
        self.assertIn("move the cell cursor there", source)
        self.assertNotIn("Name Box and press", source)

    def test_empty_cells_are_skipped_before_costing_a_record(self):
        """空单元格要在建 record 之前就跳过，否则配额照样被吃掉。"""
        source = open(runtime.__file__, encoding="utf-8").read()
        body = source[source.index("def render_visible_cells"):]
        body = body[: body.index("def render_tree")]

        skip = body.index("if not value:")
        build = body.index("record = record_for(")
        self.assertLess(skip, build, "跳过判断必须在 record_for 之前")


class ElectronActionNamesTests(AtspiPatchedTestCase):
    """Chromium/Electron 用自己的一套动作名，两个方向都会出错。"""

    def make(self, actions):
        class Node(_TreeNode):
            def get_action_iface(self):
                return object() if actions else None

            def get_n_actions(self):
                return len(actions)

            def get_action_name(self, index):
                return actions[index]

            def get_action_description(self, index):
                return ""

        return Node(role="section", x=1, y=1, w=5, h=5)

    def test_do_default_is_the_click_entry(self):
        """回归：VS Code 19 个节点的动作表是 ('doDefault', 'showContextMenu')。

        `doDefault` 就是 Chromium 的默认动作。不认这个名字，这些元素既拿不到
        `[has-click-action]`，`click_method:"accessibility"` 也会直接失败——
        整个 Electron 系应用在语义通道上等于不可点。
        """
        node = self.make(["doDefault", "showContextMenu"])
        names, has_click = runtime.node_actions(node)

        self.assertTrue(has_click)
        self.assertIsNotNone(runtime.preferred_action_index(node))
        self.assertNotIn("doDefault", names,
                         "它就是 click 本身，不该再列进 More actions")

    def test_click_ancestor_is_not_this_element_s_click(self):
        """回归：`clickAncestor` 含 click，会被子串兜底匹中——但它点的是**祖先**。

        VS Code 实测有 14 个节点是 ('clickAncestor', 'showContextMenu')。
        把它当成本元素的点击入口，agent 会以为点中了目标而实际点在别处，
        且从返回值和树里都看不出来——这是最坏的一类失败。
        """
        node = self.make(["clickAncestor", "showContextMenu"])
        _, has_click = runtime.node_actions(node)

        self.assertFalse(has_click)
        self.assertIsNone(runtime.preferred_action_index(node))

    def test_gecko_spells_it_with_a_space(self):
        """回归：同一语义在不同工具包里拼法不同。

        Chromium/Electron 叫 `clickAncestor`，Gecko/Thunderbird 叫
        `click ancestor`（带空格）。只排掉其中一种，另一种照样被子串兜底匹中，
        agent 又去点了祖先节点。实测两者都出现过。
        """
        node = self.make(["click ancestor"])
        _, has_click = runtime.node_actions(node)

        self.assertFalse(has_click)
        self.assertIsNone(runtime.preferred_action_index(node))

    def test_normalization_ignores_case_and_separators(self):
        for spelling in ("clickAncestor", "click ancestor", "Click-Ancestor",
                         "click_ancestor", "CLICKANCESTOR"):
            node = self.make([spelling])
            _, has_click = runtime.node_actions(node)
            self.assertFalse(has_click, "{!r} 应当被判为非本元素动作".format(spelling))

    def test_gecko_checkbox_state_named_actions_are_click_entries(self):
        """回归：Gecko 用结果状态命名复选框动作——勾上时是 `uncheck`、
        没勾时是 `check`，任一时刻只暴露适用的那个，调用它就等于 toggle。

        不认这两个名字，Gecko 的复选框全部退回坐标点击，而设置类界面几乎
        全是复选框（Thunderbird 5/14 个任务是账户设置、3/14 是消息过滤器）。
        """
        for spelling in ("check", "uncheck"):
            node = self.make([spelling])
            names, has_click = runtime.node_actions(node)
            self.assertTrue(has_click, "{!r} 应当算作点击入口".format(spelling))
            self.assertNotIn(spelling, names, "它就是 click 本身，不该再列进 More actions")

    def test_plain_click_still_works(self):
        node = self.make(["click", "showContextMenu"])
        _, has_click = runtime.node_actions(node)

        self.assertTrue(has_click)
        self.assertIsNotNone(runtime.preferred_action_index(node))

    def test_covered_set_has_a_single_source_of_truth(self):
        """判据只许有一份。抄成两处必然分歧——本轮加 doDefault 时就漏了副本。"""
        source = open(runtime.__file__, encoding="utf-8").read()

        self.assertEqual(source.count('"default.activate",'), 1,
                         "CLICK_COVERED_ACTIONS 不应存在副本")


class ObjectReplacementTests(AtspiPatchedTestCase):
    def test_object_replacement_placeholder_is_stripped(self):
        """回归：Chromium 用 U+FFFC 给嵌入对象占位，渲染出来是 `Value: ￼￼￼`。

        它零信息量，更糟的是**看起来像内容**——agent 会以为这个控件已经有值。
        VS Code 欢迎页实测 186 个占位符散布在 115 行里。
        """
        self.assertEqual(runtime.limit_text("￼"), "")
        self.assertEqual(runtime.limit_text("a￼￼b"), "ab")

    def test_real_text_is_untouched(self):
        self.assertEqual(runtime.limit_text("Save As…"), "Save As…")


class FocusDiagnosticTests(AtspiPatchedTestCase):
    def test_names_the_window_that_actually_holds_focus(self):
        other = _TreeNode(role="frame", name="Terminal", w=100, h=100)
        other.states.add(STATE.ACTIVE)
        app = _TreeNode(role="application", name="gnome-terminal", kids=(other,))
        original = runtime.iter_apps
        runtime.iter_apps = lambda: [app]
        self.addCleanup(setattr, runtime, "iter_apps", original)

        target = _TreeNode(role="frame", name="Editor", w=100, h=100)
        with self.assertRaises(RuntimeError) as caught:
            runtime.require_window_focus(target, "press_key")

        self.assertIn("Terminal", str(caught.exception))
        self.assertIn("gnome-terminal", str(caught.exception))

    def test_no_accessible_window_active_points_at_the_screenshot_channel(self):
        """回归：焦点被无障碍树看不见的东西拿着时，必须说出来。

        实测 VS Code：改完 settings.json 弹出原生对话框
        「A setting has changed that requires a restart to take effect.」，
        与 VS Code 同进程、锁住整个应用，**AT-SPI 里完全不存在**。
        此时 agent 看到一棵正常的树、每个动作都被正确拒绝，却无从知道原因——
        a11y 通道在这里是瞎的，只有截图能回答。
        """
        original = runtime.iter_apps
        runtime.iter_apps = lambda: []
        self.addCleanup(setattr, runtime, "iter_apps", original)

        target = _TreeNode(role="frame", name="Editor", w=100, h=100)
        with self.assertRaises(RuntimeError) as caught:
            runtime.require_window_focus(target, "press_key")

        message = str(caught.exception)
        self.assertIn("cannot see", message)
        self.assertIn("get_screenshot", message)

    def test_diagnostic_never_turns_a_clear_error_into_a_crash(self):
        """诊断代码枚举失败时必须安静退场，错误本身仍要抛出来。"""

        def boom():
            raise RuntimeError("a11y bus went away")

        original = runtime.iter_apps
        runtime.iter_apps = boom
        self.addCleanup(setattr, runtime, "iter_apps", original)

        target = _TreeNode(role="frame", name="Editor", w=100, h=100)
        with self.assertRaises(RuntimeError) as caught:
            runtime.require_window_focus(target, "press_key")

        self.assertIn("Refusing to synthesize", str(caught.exception))


class QtRichTextTests(AtspiPatchedTestCase):
    def test_qt_tooltip_html_is_reduced_to_its_message(self):
        """回归：Qt 把 tooltip 存成整段 HTML，CSS 也在里面。

        VLC 首选项实测 19 段这样的 HTML 合计 9149 字符，占整次观测的 56%，
        而真正的信息往往只有一句话。不处理的话 `Description:` 会把观测预算吃光。
        """
        blob = (
            '<html><head><meta name="qrichtext" content="1" />'
            '<style type="text/css"> p, li { white-space: pre-wrap; } </style>'
            '</head><body><p>Show a controller in fullscreen mode</p></body></html>'
        )

        self.assertEqual(
            runtime.plain_text_from_rich_text(blob),
            "Show a controller in fullscreen mode",
        )

    def test_br_becomes_a_separator_not_a_join(self):
        blob = "<html><body><p>one<br/>two</p></body></html>"

        self.assertEqual(runtime.plain_text_from_rich_text(blob), "one two")

    def test_plain_text_with_angle_brackets_is_untouched(self):
        """不对普通文本动手：真实内容里完全可能有尖括号（代码、模板、数学式），
        剥它们等于篡改 agent 读到的数据。"""
        for text in ("a < b and c > d", "List<String> items", "if x<3: pass"):
            self.assertEqual(runtime.plain_text_from_rich_text(text), text)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(runtime.plain_text_from_rich_text(None), "")
        self.assertEqual(runtime.plain_text_from_rich_text(""), "")


class PruneCostTests(AtspiPatchedTestCase):
    """裁剪判据必须**先只读它用到的字段**，不能先建整条记录。

    实测 GIMP（GAIL）：完整的 record_for 是 8.45ms/节点，而一次 render_tree
    调用它 3162 次却只产出 157 条记录——**95% 的开销当场丢弃**，占渲染耗时的
    87%，整棵树 38.4s，超过 Go 层 30s 的超时。也就是说 a11y 通道在 GIMP 上
    **默认根本用不了**，而原因只是取数顺序。改完 17.5s，行数一字不差。
    """

    def test_pruned_nodes_never_pay_for_a_full_record(self):
        # 一堆会被裁掉的无名 filler，外加一个留得下的按钮。
        fillers = [_TreeNode(role="filler", name="", x=1, y=1, w=5, h=5)
                   for _ in range(30)]
        keeper = _TreeNode(role="push button", name="OK", x=1, y=1, w=5, h=5)
        window = _TreeNode(role="frame", name="W", x=0, y=0, w=100, h=100,
                           kids=tuple(fillers) + (keeper,))

        calls = []
        original = runtime.record_for

        def counted(node, *args, **kwargs):
            calls.append(runtime.node_name(node))
            return original(node, *args, **kwargs)

        runtime.record_for = counted
        self.addCleanup(setattr, runtime, "record_for", original)

        records, _ = runtime.render_tree(window, {"x": 0, "y": 0, "width": 100, "height": 100}, [0])

        # 只有窗口自身与幸存的按钮该付全量代价。
        self.assertEqual(len(records), 2, [r["name"] for r in records])
        self.assertEqual(len(calls), 2,
                         "被裁掉的节点不该走 record_for，实际调用了 {} 次".format(len(calls)))

    def test_prune_decision_still_keeps_named_containers(self):
        """省开销不许省掉正确性：有名字的容器仍然要留住。

        实测教训——行距 combo 的 toggle button 本身没有名字，agent 只能靠父节点
        `panel Line Spacing` 指认它。裁掉这个 panel，目标虽在树里却没法被指认。
        """
        inner = _TreeNode(role="toggle button", name="", x=2, y=2, w=4, h=4)
        named_panel = _TreeNode(role="panel", name="Line Spacing", x=1, y=1, w=8, h=8,
                                kids=(inner,))
        window = _TreeNode(role="frame", name="W", x=0, y=0, w=100, h=100,
                           kids=(named_panel,))
        records, _ = runtime.render_tree(
            window, {"x": 0, "y": 0, "width": 100, "height": 100}, [0])
        self.assertIn("Line Spacing", [r["name"] for r in records])


class StateReadbackTests(unittest.TestCase):
    """动作声明了目标状态时，动作后回读校验。

    照抄 Playwright 的 `_setChecked`（dom.ts）：

        const finalState = await isChecked(progress);
        if (finalState.matches !== state)
          throw new NonRecoverableDOMError('Clicking the checkbox did not change its state');

    注意它抛的是 **NonRecoverable**——不重试。重复同一个动作不会有不同结果。
    """

    def test_action_names_come_from_measurement_not_from_playwright(self):
        """第一版这张表照 Playwright 的语义写（check/uncheck/expand/collapse），
        实测六个应用**一个都没有**，整张表是死代码。

        真实存在的是 `expand or contract`(119)、`toggle`(2) 这些，
        而且它们是**翻转**语义不是"设成某值"。
        """
        for dead in ("check", "uncheck", "expand", "collapse", "select", "deselect"):
            self.assertNotIn(dead, runtime.ACTION_MUST_FLIP, dead)
        self.assertIn("expand or contract", runtime.ACTION_MUST_FLIP)
        self.assertIn("toggle", runtime.ACTION_MUST_FLIP)

    def test_state_flipped_as_promised(self):
        note, failed = runtime.state_transition_note(
            "toggle", {"CHECKED": False}, {"CHECKED": True})
        self.assertFalse(failed)
        self.assertIn("CHECKED flipped False -> True", note)

    def test_boundary_is_stated_not_hidden(self):
        """**状态到位 ≠ 行为发生。**

        本仓库实测过反例：VLC 首选项里那颗单选按钮，Toggle 之后 CHECKED 真的
        翻转了，面板却不切换。这条判据接不住那一类，措辞里必须讲明白——
        不然它就成了新的假阳性来源。
        """
        note, _ = runtime.state_transition_note(
            "toggle", {"CHECKED": False}, {"CHECKED": True})
        self.assertIn("proves the CONTROL changed", note)
        self.assertIn("VLC", note)

    def test_state_did_not_change_is_a_hard_failure(self):
        note, failed = runtime.state_transition_note(
            "expand or contract", {"EXPANDED": False}, {"EXPANDED": False})
        self.assertTrue(failed)
        self.assertIn("promises to flip it", note)
        # 必须明说"别重试"，并给出下一条可走的路。
        self.assertIn("Repeating the same call will not help", note)
        self.assertIn("click_xy", note)

    def test_actions_that_declare_nothing_are_not_judged(self):
        """`click` / `menu` 这类动作没声明目标状态，回读无从谈起——
        **判不了就别判**，硬判会制造假阳性。"""
        # 这些是实测里最常见的动作名，它们都不承诺改变任何可读状态。
        for action in ("click", "menu", "activate", "press", "open", "dodefault", ""):
            note, failed = runtime.state_transition_note(
                action, {"CHECKED": False}, {"CHECKED": True})
            self.assertIsNone(note, action)
            self.assertFalse(failed, action)

    def test_unreadable_state_is_not_treated_as_failure(self):
        note, failed = runtime.state_transition_note("toggle", None, {"CHECKED": True})
        self.assertIsNone(note)
        self.assertFalse(failed)
        note, failed = runtime.state_transition_note("toggle", {"CHECKED": False}, {})
        self.assertIsNone(note)
        self.assertFalse(failed)


class StableIndexTests(unittest.TestCase):
    """编号要跨快照存活。照抄 Playwright 的 ariaSnapshot.ts。

    修的是实测踩过的最贵一类失败：F4 打开对话框后索引全部重排，用旧下标调
    click 时工具照点不误——本想点 Position Y，实际点到菜单，把对象高度误改成
    16.26cm，**全程零报错**。
    """

    def test_same_element_keeps_its_number(self):
        known = {"0.1": {"index": 7, "role": "push button", "name": "Save"}}
        indexer = runtime.StableIndexer(known)
        self.assertEqual(indexer.index_for([0, 1], "push button", "Save"), 7)

    def test_role_or_name_change_invalidates_the_number(self):
        """失效条件照抄 Playwright：role 或 name 变了就重新发号——
        因为它已经不是"同一个东西"了。"""
        known = {"0.1": {"index": 7, "role": "push button", "name": "Save"}}
        self.assertNotEqual(
            runtime.StableIndexer(known).index_for([0, 1], "push button", "Saved"), 7)
        self.assertNotEqual(
            runtime.StableIndexer(known).index_for([0, 1], "menu item", "Save"), 7)

    def test_inserting_an_element_does_not_shift_the_others(self):
        """这是遍历序号最致命的毛病：插一个元素，它后面所有编号全推走。"""
        known = {
            "0.0": {"index": 0, "role": "frame", "name": "W"},
            "0.1": {"index": 1, "role": "push button", "name": "A"},
            "0.2": {"index": 2, "role": "push button", "name": "B"},
        }
        indexer = runtime.StableIndexer(known)
        self.assertEqual(indexer.index_for([0, 0], "frame", "W"), 0)
        # 中间插进来一个新元素——它拿新号，不占用别人的
        fresh = indexer.index_for([0, 1, 5], "push button", "NEW")
        self.assertEqual(indexer.index_for([0, 1], "push button", "A"), 1)
        self.assertEqual(indexer.index_for([0, 2], "push button", "B"), 2)
        self.assertGreaterEqual(fresh, 3)

    def test_numbers_are_never_reused_within_one_snapshot(self):
        """同一份快照里两个元素拿到同一个号 = 静默指错对象。"""
        known = {"0.1": {"index": 3, "role": "text", "name": ""},
                 "0.2": {"index": 3, "role": "text", "name": ""}}
        indexer = runtime.StableIndexer(known)
        first = indexer.index_for([0, 1], "text", "")
        second = indexer.index_for([0, 2], "text", "")
        self.assertNotEqual(first, second)

    def test_without_prior_knowledge_it_numbers_from_zero(self):
        indexer = runtime.StableIndexer(None)
        self.assertEqual(indexer.index_for([0], "frame", "W"), 0)
        self.assertEqual(indexer.index_for([0, 0], "push button", "A"), 1)


class DenseIndexTests(AtspiPatchedTestCase):
    """被裁掉的节点**不能先领号再被丢掉**。

    必须继承 AtspiPatchedTestCase：裸 TestCase 下 relative_frame 全返回 None，
    于是 depth>0 的节点一律被判成不可见而裁光，树里只剩根节点——
    那种 fixture 测不出任何裁剪行为。这一条是写测试时当场踩到的。
    """

    def test_pruned_nodes_do_not_consume_numbers(self):
        """被裁掉的节点**不能先领号再被丢掉**。

        原来发号在裁剪判断之前，于是号被消耗、节点却不进 records/refs：
        打印出来的下标稀疏带洞。实测 Nautilus 一次快照 91 个元素、
        下标散布在 0..670——7.4 倍膨胀。而且被裁节点不在 known 里，
        每次快照都重新领一批新号，号会随会话单调涨下去。

        判据只看**稠密**：树里有 N 个元素，下标就该落在 0..N-1。
        """
        # 一堆无名 panel（会被角色裁剪丢掉）夹着两个有名按钮。
        # 几何必须显式给，裁剪判据的第一关就是 frame 不为 None。
        filler = [_TreeNode(role="panel", x=1, y=1, w=5, h=5)
                  for _ in range(8)]
        keep_a = _TreeNode(role="push button", name="Save", x=2, y=2, w=5, h=5)
        keep_b = _TreeNode(role="push button", name="Cancel", x=3, y=3, w=5, h=5)
        window = _TreeNode(role="frame", name="W", w=900, h=600,
                           kids=tuple(filler[:4]) + (keep_a,)
                                + tuple(filler[4:]) + (keep_b,))

        # 必须传 indexer——不传的话 index 退回 len(records)，那本来就是稠密的，
        # 测不出任何东西。
        records, _lines = runtime.render_tree(
            window, None, [0], indexer=runtime.StableIndexer({}), prune=True)
        indices = sorted(r["index"] for r in records)
        self.assertEqual(indices, list(range(len(records))),
                         "下标应当稠密，实际 {}".format(indices))



class TruncatedNameIdentityTests(AtspiPatchedTestCase):
    """name 被 text_limit 截断之后，元素身份**不能悄悄退化**。

    实测复现：同一个节点、同一个名字，text_limit 从 500 调到 40，
    record_still_matches 就从 True 翻成 False——因为它拿实时的完整 name
    去比 record 里已截断的 name。失配之后静默退到最弱的"role + 屏幕位置"判据。

    也就是说：**agent 为省 token 调低 text_limit，会悄悄削弱元素身份**。
    阈值是每次请求可传的参数，所以比较时不能假定它等于默认值。
    """

    LONG = "Manage — Settings and keyboard shortcuts, and more actions for the editor"

    def test_truncated_name_still_identifies_the_same_element(self):
        node = _TreeNode(role="push button", name=self.LONG)
        for limit in (500, 40, 10):
            record = runtime.record_for(node, 7, [0, 1], None, text_limit=limit)
            self.assertTrue(
                runtime.record_still_matches(node, record, None),
                "text_limit={} 时同一个节点没认出来".format(limit))

    def test_a_different_element_sharing_the_prefix_is_not_confused(self):
        """修截断不能换来一个静默误判。

        只按截断后的前缀比的话，两个前缀相同的不同元素会被判成同一个——
        那等于把一个静默失配换成一个静默误判，更糟。所以截断的记录
        随身带完整名字的指纹，判定回到"完整名字精确相等"。
        """
        node = _TreeNode(role="push button", name=self.LONG)
        sibling = _TreeNode(
            role="push button",
            name="Manage — Settings and keyboard shortcuts, and MORE actions here")
        for limit in (40, 10):
            record = runtime.record_for(node, 7, [0, 1], None, text_limit=limit)
            self.assertFalse(
                runtime.record_still_matches(sibling, record, None),
                "text_limit={} 时前缀相同的另一个元素被误判成同一个".format(limit))

    def test_short_names_carry_no_fingerprint(self):
        """没截断就不该多带字段——指纹只为截断的记录存在。"""
        node = _TreeNode(role="push button", name="Save")
        record = runtime.record_for(node, 0, [0], None, text_limit=500)
        self.assertNotIn("nameHash", record)

class SnapshotGrammarTests(unittest.TestCase):
    """快照文法。借鉴 Playwright 的 aria snapshot（`- role "name" [attr=value]`）。

    最要紧的一条是**自由文本必须加引号**。旧格式是
    `<idx> <role> <name> Description: <desc>`，而名字本身可以含冒号——
    实测 LibreOffice Impress 里就有 `panel PageShape: Weekday in school`。
    于是"名字"与"字段分隔符"在词法上无法区分，我们发出去的是一种歧义文法。
    """

    def _line(self, **overrides):
        record = {
            "index": 7, "localizedControlType": "push button", "controlType": "push button",
            "name": "", "automationId": "", "value": "", "states": "",
            "description": "", "placeholder": "", "actions": [], "frame": None,
        }
        record.update(overrides)
        return runtime.render_element_line(record, 0)

    def test_name_containing_a_colon_stays_unambiguous(self):
        """回归：这正是旧格式解析不了的那一行。"""
        line = self._line(name="PageShape: Weekday in school",
                          description="Slide")
        self.assertIn('"PageShape: Weekday in school"', line)
        self.assertIn('[desc="Slide"]', line)

    def test_quotes_inside_a_name_are_escaped(self):
        line = self._line(name='say "hi"')
        self.assertIn(r'"say \"hi\""', line)

    def test_geometry_is_compact(self):
        """`Frame: {x: 687, y: 23, width: 64, height: 46}` 45 字符 → 14 字符。

        实测几何占整棵树的 35–50%，压缩后全树省约 30%，信息不少一个字。
        """
        line = self._line(frame={"x": 687.0, "y": 23.0, "width": 64.0, "height": 46.0})
        self.assertIn("{687,23,64,46}", line)
        self.assertNotIn("Frame:", line)

    def test_value_goes_last_after_a_colon(self):
        """对齐 aria 的 `- textbox: Enter your name`。"""
        line = self._line(localizedControlType="text", value="baseline-marker",
                          states="[focused]")
        self.assertTrue(line.rstrip().endswith(': "baseline-marker"'), line)

    def test_empty_segments_are_omitted(self):
        """空的段不留空壳——否则每行都拖一串没有信息的字节。"""
        self.assertEqual(self._line(name="OK"), '\t7 push button "OK"')

    def test_index_stays_at_the_head(self):
        """我们没有 selector，下标是唯一的引用手段，必须好取。"""
        line = self._line(name="OK").strip()
        self.assertTrue(line.startswith("7 "), line)


class PixelEvidenceTests(unittest.TestCase):
    """像素比对是**独立于树**的第三种效果判据。

    起因是实测过的误判：ctrl+s 之后树字节不变，工具断言"送达但被忽略"，
    而文件其实存下来了；agent 因此多花两步自证。树看不见整类效果
    （格式改动、文件状态、画布像素），所以"树没变"推不出"没生效"。
    """

    def _frame(self, fill, size=(80, 60)):
        width, height = size
        stride = width * 3
        return {"data": bytes([fill]) * (stride * height), "stride": stride,
                "channels": 3, "width": width, "height": height}

    def test_identical_frames_report_no_change(self):
        frame = self._frame(10)
        note = runtime.pixel_change_note(
            runtime.persistent_pixel_change(frame, self._frame(10), self._frame(10)))
        self.assertIn("pixel-identical", note)

    def test_resize_is_itself_evidence(self):
        """窗口尺寸变了就没法逐点比，但那本身就是"发生了事"的证据。"""
        grown = self._frame(10, size=(90, 60))
        note = runtime.pixel_change_note(
            runtime.persistent_pixel_change(self._frame(10), grown, grown))
        self.assertIn("changed size or position", note)
        self.assertIn("itself evidence", note)

    def test_substantial_change_is_reported_with_a_region(self):
        after = self._frame(10)
        data = bytearray(after["data"])
        for y in range(0, 60):
            for x in range(20, 60):
                offset = y * after["stride"] + x * 3
                data[offset:offset + 3] = b"\xff\xff\xff"
        after["data"] = bytes(data)
        note = runtime.pixel_change_note(
            runtime.persistent_pixel_change(self._frame(10), after, after))
        self.assertIn("% of the window changed", note)
        self.assertIn("concentrated in", note)

    def _dots(self, count, size, offset_rows=1):
        frame = self._frame(10, size=size)
        data = bytearray(frame["data"])
        for i in range(count):
            offset = (8 * (i + offset_rows)) * frame["stride"] + 8 * 3
            data[offset:offset + 3] = b"\xff\xff\xff"
        frame["data"] = bytes(data)
        return frame

    def test_flicker_is_filtered_by_requiring_the_change_to_persist(self):
        """闪烁的东西只在其中一张动作后画面里变了，不该算作效果。

        这是走过弯路才定下的判据。第一版设了个固定阈值（猜的），第二版改成
        "动作前连抓两张测噪声底"——方向对，但两张只隔几毫秒，1Hz 的文本光标
        根本没来得及闪，噪声底测出来是 0，于是**空操作也被判成"屏幕变了"**，
        变化区域正是那个 8x16 的光标。

        现在照 Playwright"连续两张一致再比"的思路：只认两张里都存在的变化。
        """
        size = (1600, 1000)
        before = self._frame(10, size=size)
        blink_on = self._dots(1, size)          # 光标亮
        blink_off = self._frame(10, size=size)  # 光标灭，与动作前一致

        change = runtime.persistent_pixel_change(before, blink_on, blink_off)
        self.assertEqual(change["changed"], 0, "闪烁不该算作变化")
        self.assertIn("pixel-identical", runtime.pixel_change_note(change))

    def test_a_real_change_persists_across_both_captures(self):
        size = (1600, 1000)
        before = self._frame(10, size=size)
        after = self._dots(30, size)

        change = runtime.persistent_pixel_change(before, after, after)
        self.assertEqual(change["changed"], 30)
        note = runtime.pixel_change_note(change)
        self.assertIn("STAYED changed across two captures", note)
        self.assertIn("concentrated in", note)

    def test_resize_between_captures_is_reported_not_guessed(self):
        size = (1600, 1000)
        before = self._frame(10, size=size)
        grown = self._frame(10, size=(1608, 1000))
        change = runtime.persistent_pixel_change(before, grown, grown)
        self.assertTrue(change["resized"])
        self.assertIn("itself evidence", runtime.pixel_change_note(change))

    def test_missing_capture_never_pretends_to_have_compared(self):
        """抓不到图就返回 None——**不许假装比过**。"""
        frame = self._frame(10)
        self.assertIsNone(runtime.persistent_pixel_change(None, frame, frame))
        self.assertIsNone(runtime.persistent_pixel_change(frame, None, frame))
        self.assertIsNone(runtime.persistent_pixel_change(frame, frame, None))
        self.assertIsNone(runtime.pixel_change_note(None))


class _FakeClock:
    """假时钟。sleep 直接推进时间，测试因此既确定又不真的等。"""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _WindowNode(_TreeNode):
    def __init__(self, role, name, active=False, modal=False):
        super().__init__(role=role, name=name)
        if active:
            self.states.add(STATE.ACTIVE)
        if modal:
            self.states.add(STATE.MODAL)


class _ScriptedApp(_TreeNode):
    """顶层窗口集合随假时钟推进而变化的应用节点。

    timeline 形如 [(生效时刻, [窗口...]), ...]，按时刻升序；读取时取最后一条
    已经生效的。这样就能把实测到的时序原样搬进测试。
    """

    def __init__(self, clock, timeline):
        super().__init__(role="application", name="app")
        self.clock = clock
        self.timeline = timeline

    def _windows(self):
        current = []
        for at, windows in self.timeline:
            if self.clock.now >= at:
                current = windows
        return current

    def get_child_count(self):
        return len(self._windows())

    def get_child_at_index(self, index):
        return self._windows()[index]


class SettleWaitTests(AtspiPatchedTestCase):
    """动作后的安置等待。

    这一组守的是一个**静默操作错误窗口**的竞态：新窗口已经进树、但 ACTIVE
    还挂在旧窗口上的那几十毫秒里，快照会照到旧窗口，而树本身完全自洽，
    没有任何一处看得出不对。
    """

    def _wait(self, app, before, clock, **kwargs):
        return runtime.wait_for_ui_to_settle(
            app,
            before,
            clock=clock.monotonic,
            sleep=clock.sleep,
            **kwargs
        )

    def test_returns_right_after_the_minimum_wait_when_nothing_opened(self):
        """常见路径不能变慢：窗口集合没变就只等最短安置时间。"""
        clock = _FakeClock()
        main = _WindowNode("frame", "Inbox", active=True)
        app = _ScriptedApp(clock, [(0.0, [main])])
        before = runtime.window_identity_set(app)

        self.assertIsNone(self._wait(app, before, clock))
        self.assertAlmostEqual(clock.now, runtime.SETTLE_MIN_SECONDS, places=6)

    def test_waits_until_the_newly_opened_window_takes_focus(self):
        """回归：实测到的 Thunderbird `Tools → Message Filters` 时序。

            t=0.070  新窗口进树，ACTIVE 还在主窗口身上
            t=0.123  ACTIVE 才转移过去

        原先固定 sleep(0.12) 正好压在 0.123 这个边界上，于是快照有概率照到
        主窗口——agent 按索引点 `New…`，实际点出的是主窗口的「新建邮件」。
        """
        clock = _FakeClock()
        main_active = _WindowNode("frame", "Inbox", active=True)
        main_idle = _WindowNode("frame", "Inbox")
        filters_idle = _WindowNode("frame", "Message Filters")
        filters_active = _WindowNode("frame", "Message Filters", active=True)
        app = _ScriptedApp(
            clock,
            [
                (0.0, [main_active]),
                (0.070, [main_active, filters_idle]),
                (0.123, [main_idle, filters_active]),
            ],
        )
        before = runtime.window_identity_set(app)

        self.assertIsNone(self._wait(app, before, clock))
        self.assertGreaterEqual(clock.now, 0.123)
        # 真正要守住的不是"等够了"，而是等完之后 main_window() 挑对了窗口。
        _, chosen = runtime.main_window(app)
        self.assertEqual(runtime.node_name(chosen), "Message Filters")

    def test_the_old_fixed_sleep_would_have_picked_the_wrong_window(self):
        """把旧行为钉住：这就是修之前会发生的事，不是假想出来的风险。"""
        clock = _FakeClock()
        main_active = _WindowNode("frame", "Inbox", active=True)
        filters_idle = _WindowNode("frame", "Message Filters")
        app = _ScriptedApp(
            clock,
            [(0.0, [main_active]), (0.070, [main_active, filters_idle])],
        )

        clock.sleep(0.12)  # 旧代码：动作后固定睡 0.12 就建快照
        _, chosen = runtime.main_window(app)
        self.assertEqual(runtime.node_name(chosen), "Inbox")

    def test_modal_window_counts_as_settled(self):
        """模态框可能只报 MODAL 不报 ACTIVE，两者都算焦点已落定。

        出现时刻取实测的 LibreOffice 0.045s。
        """
        clock = _FakeClock()
        main = _WindowNode("frame", "Writer", active=True)
        alert = _WindowNode("alert", "Question", modal=True)
        app = _ScriptedApp(clock, [(0.0, [main]), (0.045, [main, alert])])
        before = runtime.window_identity_set(app)

        self.assertIsNone(self._wait(app, before, clock))
        _, chosen = runtime.main_window(app)
        self.assertEqual(runtime.node_name(chosen), "Question")

    def test_waits_for_focus_to_come_back_after_a_window_closes(self):
        """关窗口同样有空档：对话框没了，焦点还没回到主窗口。"""
        clock = _FakeClock()
        main_idle = _WindowNode("frame", "Writer")
        main_active = _WindowNode("frame", "Writer", active=True)
        dialog = _WindowNode("dialog", "Find and Replace", active=True)
        app = _ScriptedApp(
            clock,
            [
                (0.0, [main_idle, dialog]),
                (0.05, [main_idle]),
                (0.30, [main_active]),
            ],
        )
        before = runtime.window_identity_set(app)

        self.assertIsNone(self._wait(app, before, clock))
        self.assertGreaterEqual(clock.now, 0.30)

    def test_window_opening_after_the_minimum_wait_is_out_of_scope(self):
        """把边界钉住，别让它变成一个被误以为已覆盖的情形。

        实测六例开窗口进树都 ≤ 0.070s，SETTLE_MIN_SECONDS=0.12 有 1.7 倍余量。
        比这更慢的应用会漏——漏掉之后是**另一种**失败：快照照到动作前的状态，
        新窗口根本不出现，agent 看得见。与这里修的"树自洽却照错窗口"不同。

        谁要是调小 SETTLE_MIN_SECONDS，这条会先炸。
        """
        clock = _FakeClock()
        main = _WindowNode("frame", "Inbox", active=True)
        slow = _WindowNode("frame", "Slow Dialog")
        app = _ScriptedApp(
            clock,
            [(0.0, [main]), (runtime.SETTLE_MIN_SECONDS + 0.05, [main, slow])],
        )
        before = runtime.window_identity_set(app)

        self.assertIsNone(self._wait(app, before, clock))
        self.assertAlmostEqual(clock.now, runtime.SETTLE_MIN_SECONDS, places=6)
        self.assertGreaterEqual(runtime.SETTLE_MIN_SECONDS, 0.070)

    def test_reports_when_the_new_window_never_takes_focus(self):
        """超时不许静默：快照可能照的不是 agent 以为的那个窗口，得说出来。"""
        clock = _FakeClock()
        main = _WindowNode("frame", "Inbox", active=True)
        tooltip = _WindowNode("window", "")
        app = _ScriptedApp(clock, [(0.0, [main]), (0.05, [main, tooltip])])
        before = runtime.window_identity_set(app)

        note = self._wait(app, before, clock, timeout_seconds=0.4)
        self.assertIsNotNone(note)
        self.assertIn("element_index", note)
        # 有界：不许把工具挂住。
        self.assertLess(clock.now, runtime.SETTLE_MIN_SECONDS + 0.4 + 0.1)

    def test_missing_baseline_skips_the_wait(self):
        """取不到动作前的窗口集合时，退回旧行为，不要凭空等。"""
        clock = _FakeClock()
        app = _ScriptedApp(clock, [(0.0, [_WindowNode("frame", "Inbox")])])

        self.assertIsNone(self._wait(app, None, clock))
        self.assertAlmostEqual(clock.now, runtime.SETTLE_MIN_SECONDS, places=6)


class ScreenshotPolicyTests(unittest.TestCase):
    """a11y 轨带不带截图，以及哪些工具的截图不可关。

    默认改成带图，是因为"两条独立轨道"这个前提被实测推翻了：树给过两次假阴性
    （右对齐、保存都生效了却被判成"送达但被忽略"），都是一张截图判掉的。
    """

    def setUp(self):
        self._saved = os.environ.get("OPEN_COMPUTER_USE_A11Y_SCREENSHOTS")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("OPEN_COMPUTER_USE_A11Y_SCREENSHOTS", None)
        else:
            os.environ["OPEN_COMPUTER_USE_A11Y_SCREENSHOTS"] = self._saved

    def test_enabled_by_default(self):
        os.environ.pop("OPEN_COMPUTER_USE_A11Y_SCREENSHOTS", None)
        self.assertTrue(runtime.a11y_screenshots_enabled())

    def test_can_be_switched_off_for_the_ab_test(self):
        for value in ("0", "false", "no", "off", "OFF", " 0 "):
            os.environ["OPEN_COMPUTER_USE_A11Y_SCREENSHOTS"] = value
            self.assertFalse(runtime.a11y_screenshots_enabled(), value)

    def test_unrecognised_values_keep_screenshots_on(self):
        """开关只认明确的关闭值。拼错了应该维持默认，而不是静默省掉截图。"""
        for value in ("1", "true", "yes", "", "maybe"):
            os.environ["OPEN_COMPUTER_USE_A11Y_SCREENSHOTS"] = value
            self.assertTrue(runtime.a11y_screenshots_enabled(), value)

    def test_gui_channel_screenshot_is_not_negotiable(self):
        """GUI 通道两头都够不着 a11y：不锚定元素，效果也未必进树。

        实测把 Impress 标题从 0.76cm 拖到 15.00cm，元素的 Frame 一点没变。
        所以哪怕 A/B 把 a11y 轨的截图关了，drag 也必须带图。
        """
        self.assertEqual(runtime.SCREENSHOT_REQUIRED_TOOLS, {"drag_xy", "click_xy"})
        os.environ["OPEN_COMPUTER_USE_A11Y_SCREENSHOTS"] = "0"
        self.assertFalse(runtime.a11y_screenshots_enabled())
        # perform_operation 里的判据：required 的工具传 True，其余传 None 走策略。
        for tool, expected in (("drag_xy", True), ("click_xy", True),
                               ("click", None), ("press_key", None)):
            forced = True if tool in runtime.SCREENSHOT_REQUIRED_TOOLS else None
            self.assertEqual(forced, expected, tool)


class _NoSleep:
    """让 perform_operation 里的固定 sleep 不拖慢测试。"""

    @staticmethod
    def sleep(_seconds):
        return None

    @staticmethod
    def time():
        return 0.0


if __name__ == "__main__":
    unittest.main(verbosity=2)


class _FakeRelation:
    def __init__(self, kind, targets):
        self.kind = kind
        self.targets = list(targets)

    def get_relation_type(self):
        return self.kind

    def get_n_targets(self):
        return len(self.targets)

    def get_target(self, index):
        return self.targets[index]


class _NamedNode(FakeNode):
    """带名字、可带 LABELLED_BY 关系的假节点。"""

    def __init__(self, name="", role="", relations=(), **kwargs):  # noqa: D107
        super().__init__(**kwargs)
        self._name = name
        self._role = role
        self._relations = list(relations)

    def get_name(self):
        return self._name

    def get_role_name(self):
        return self._role

    def get_relation_set(self):
        return self._relations


class LabelledByTests(AtspiPatchedTestCase):
    """无名控件从 LABELLED_BY 借名字。

    判据来自实测：LibreOffice 7.3「位置和大小」对话框里 13 个 spin button
    全部无名，13 个都能靠这条关系拿到名字（Position Y / Position X /
    Width / Height / Angle / Radius）。定位方式因此从"数第几个"变成"按名字选"。
    """

    def _labelled(self, label_text, own_name=""):
        label = _NamedNode(name=label_text, role="label")
        kind = runtime.Atspi.RelationType.LABELLED_BY
        return _NamedNode(
            name=own_name,
            role="spin button",
            relations=[_FakeRelation(kind, [label])],
        )

    def test_unnamed_control_borrows_the_label(self):
        node = self._labelled("Position Y:")
        # 尾冒号要去掉：选择器里写冒号很别扭，而冒号不携带信息
        self.assertEqual(runtime.labelled_by_name(node), "Position Y")
        self.assertEqual(runtime.effective_name(node), "Position Y")

    def test_a_control_with_its_own_name_does_not_borrow(self):
        # 增益全部落在无名节点上。一个已经叫 Save 的按钮再关联一个 Save 标签，
        # 对 agent 没有新信息，却要付一次 DBus 往返（实测 1.037ms/节点）。
        node = self._labelled("Position Y:", own_name="Save")
        self.assertEqual(runtime.effective_name(node), "Save")

    def test_identity_comparison_uses_the_same_name_as_the_record(self):
        """这是本条改动最容易复发的坑，必须钉死。

        commit 5543a52 修过同型问题：record 里存一种名字、重解析时用另一种去比，
        必然失配；而失配是**静默**的，身份判据会悄悄退到最弱的"role + 屏幕位置"，
        元素一动就指向别人。

        借名字会原样重现它：记录里写着 Position Y，而 node_name() 在同一个节点上
        返回空串。所以两边必须走同一个 effective_name()。
        """
        node = self._labelled("Position Y:")
        record = {"name": "Position Y"}
        self.assertTrue(runtime.record_name_matches(node, record))
        # 反证：如果活节点侧改用自身名字（空串），这条断言就会失败
        self.assertFalse(runtime.record_name_matches(node, {"name": "Something Else"}))

    def test_borrowed_names_are_marked_so_the_source_is_visible(self):
        # 同一个「位置和大小」对话框里 Position Y 出现了**两次**（两个标签页各一个）。
        # 调用方需要知道这个名字是借来的、因此可能不唯一。
        marks = runtime.state_segment(FakeNode(), borrowed_name=True)
        self.assertIn("labelled", marks)
        self.assertNotIn("labelled", runtime.state_segment(FakeNode()))


class ModalDiagnosticTests(AtspiPatchedTestCase):
    """挡在前面的对话框要明说。分两档，因为证据强度不同。"""

    def _window(self, name, role, states):
        return _NamedNode(name=name, role=role, states=states)

    def _app(self, *windows):
        return _NamedNode(name="app", role="application", children=windows)

    def test_modal_state_licenses_the_strong_claim(self):
        dialog = self._window("Position and Size", "dialog",
                              [STATE.MODAL, STATE.SHOWING, STATE.ACTIVE])
        main = self._window("Doc - Impress", "frame", [STATE.SHOWING])
        notes = runtime.modal_diagnostic(dialog, self._app(dialog, main))
        self.assertEqual(len(notes), 1)
        self.assertIn("MODAL DIALOG", notes[0])
        # 有 MODAL 位才能断言"应用会忽略其它窗口的输入"
        self.assertIn("ignore input to every other window", notes[0])
        # 要说清挡住了谁——agent 需要知道回哪儿去
        self.assertIn("Doc - Impress", notes[0])

    def test_a_dialog_without_the_modal_bit_gets_the_weaker_claim(self):
        """实测逼出来的一档。

        LibreOffice 7.3 的「Tip of the Day」是 role=dialog、ACTIVE、SHOWING，
        **却不设 MODAL**，而它确确实实挡在应用前面。MODAL 位在 Linux 上和
        ENABLED 一样，不同工具包设不设全凭自觉。

        只认 MODAL 会漏掉真实阻塞；把两档混为一谈，则是替一个不设 MODAL 的
        对话框打我们无权打的包票。
        """
        dialog = self._window("Tip of the Day: 1/223", "dialog",
                              [STATE.SHOWING, STATE.ACTIVE])
        main = self._window("Doc - Impress", "frame", [STATE.SHOWING])
        notes = runtime.modal_diagnostic(dialog, self._app(dialog, main))
        self.assertEqual(len(notes), 1)
        self.assertIn("DIALOG IN FRONT", notes[0])
        # 不许出现强断言
        self.assertNotIn("ignore input to every other window", notes[0])
        self.assertIn("not proof that the app is blocked", notes[0])

    def test_a_plain_main_window_says_nothing(self):
        main = self._window("Doc - Impress", "frame", [STATE.SHOWING, STATE.ACTIVE])
        self.assertEqual(runtime.modal_diagnostic(main, self._app(main)), [])

    def test_a_lone_dialog_is_the_main_interface_not_a_blocker(self):
        # 应用只有这一个对话框窗口时，它就是主界面。说"它挡着什么"是无中生有。
        only = self._window("Preferences", "dialog", [STATE.SHOWING, STATE.ACTIVE])
        self.assertEqual(runtime.modal_diagnostic(only, self._app(only)), [])


class ResolvedNoteTests(AtspiPatchedTestCase):
    """动作要说清作用在了谁身上。"""

    def test_reports_index_role_and_name(self):
        node = _NamedNode(name="Save", role="push button")
        note = runtime.resolved_note(node, {"index": 4, "controlType": "push button",
                                            "name": "Save"})
        self.assertEqual(note, "Resolved element_index 4 to push button 'Save'.")

    def test_drift_from_the_snapshot_is_a_warning_not_a_silent_pass(self):
        """这是本条真正的价值所在。

        解析是多级回退的（runtimeId 路径 → automationId → name+role → role+几何）。
        record_still_matches 里记着 Nautilus 的实例：菜单关掉之后同一个 index 9
        解析到了工具栏的"切换视图选项"，于是"重命名"变成了别的操作，
        而且一路 isError=False——静默操作错误的控件是最坏的失败模式。
        """
        node = _NamedNode(name="Menu", role="toggle button")
        note = runtime.resolved_note(node, {"index": 9, "controlType": "menu item",
                                            "name": "Rename"})
        self.assertIn("WARNING", note)
        self.assertIn("'menu item'", note)
        self.assertIn("'toggle button'", note)
        self.assertIn("'Rename'", note)

    def test_unnamed_targets_still_get_a_line(self):
        node = _NamedNode(name="", role="canvas")
        note = runtime.resolved_note(node, {"index": 7, "controlType": "canvas"})
        self.assertEqual(note, "Resolved element_index 7 to an unnamed canvas.")

    def test_coordinate_actions_have_no_element_to_report(self):
        # click_xy 不定位任何元素，硬报一条只会是噪声
        self.assertIsNone(runtime.resolved_note(None, {"index": 1}))
        self.assertIsNone(runtime.resolved_note(_NamedNode(), None))


class ProcessNameAliasTests(unittest.TestCase):
    """应用名可以用**进程名**去猜——以及别把网页地址当成进程名。

    第一条来自轨迹：`appNotFound("file-roller")`，而那个应用的 a11y 名是
    `Archive Manager`。两个名字之间没有公共子串，分隔符归一化再宽松也救不回来，
    但 `file-roller` 恰恰就是它的进程名。真机上同类的还有
    org.gnome.Software / snap-store。

    第二条是我加第一条时**自己引入的**：cmdline 的约定是 \0 分隔，但 Chrome
    那条读出来是一整块 `chrome --remote-debugging-port=1337`（空格分隔）。
    只按 \0 切再取 basename，就会从 `…/drugs.com` 里读出 "drugs.com"，
    把一个网页地址注册成 Chrome 的别名——工具凭空编出一个不存在的应用名。
    这两条都钉在这里。
    """

    def _process_names(self, blob, comm="x"):
        # 不去补丁 builtins.open——那既脆又会波及无关代码。process_names 接受
        # 一个 proc_root，测试造一棵假的 /proc 就够了。
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            pid_dir = os.path.join(root, "4242")
            os.makedirs(pid_dir)
            with open(os.path.join(pid_dir, "comm"), "w") as handle:
                handle.write(comm + "\n")
            with open(os.path.join(pid_dir, "cmdline"), "wb") as handle:
                handle.write(blob)
            return runtime.process_names(4242, proc_root=root)

    def test_null_separated_cmdline_gives_binary_name(self):
        names = self._process_names(b"/usr/bin/file-roller\0/tmp/a.zip\0",
                                    comm="file-roller")
        self.assertIn("file-roller", names)

    def test_space_separated_cmdline_does_not_leak_a_url(self):
        # 真机上 Chrome 就是这样：整块、空格分隔、末尾是访问过的地址。
        names = self._process_names(
            b"/opt/google/chrome/chrome --app=https://www.drugs.com",
            comm="chrome")
        self.assertIn("chrome", names)
        self.assertNotIn("drugs.com", names)
        self.assertNotIn("drugs", names)

    def test_suffixed_binary_also_registers_its_stem(self):
        # soffice.bin 要能被 "soffice" 猜中。
        names = self._process_names(b"/usr/lib/libreoffice/program/soffice.bin\0",
                                    comm="soffice.bin")
        self.assertIn("soffice.bin", names)
        self.assertIn("soffice", names)


class HiddenMenuItemsTests(AtspiPatchedTestCase):
    """菜单的子项一个都没渲染出来时，要在这个菜单自己身上说明白。

    出处是第 47、48 题：agent 要把 GIMP 里的图导出，而树里找不到任何
    Export/Save As，只能得出"这个应用没有导出"的结论。真相是 File 底下就有
    `Export to <文件名>`，只是那 39 个菜单项自己不 SHOWING，被逐个裁掉了。

    原有的 "N items collapsed" 提示救不了这种情况，因为它的判据是「菜单自己不
    SHOWING」——而 GIMP 的 File 菜单**是** SHOWING 的，它就挂在菜单栏上。
    树末尾那句全局的 "N node(s) omitted" 也救不了：它不指名道姓，
    没人能从中知道是哪个菜单藏了东西。
    """

    def _hidden(self):
        """造一个"不在屏幕上"的菜单项——GIMP 未打开的菜单项就是这个状态：
        AT-SPI 里它存在、有名字，但没有可见的几何。"""
        node = _TreeNode(role="menu item", name="Export As...", w=0, h=0)
        node.states = set()
        return node

    def test_menu_with_all_children_pruned_says_how_many(self):
        item = self._hidden()
        menu = _TreeNode(role="menu", name="File", w=40, h=20, kids=(item,))
        root = _TreeNode(role="frame", name="GIMP", w=100, h=100, kids=(menu,))

        _records, lines = runtime.render_tree(root, None, [0], prune=True)
        text = "\n".join(lines)

        self.assertIn('menu "File"', text)
        # 数字要对得上，措辞要说清"打开之后才看得见"。
        self.assertIn("1 items not listed", text)
        self.assertIn("after this menu is opened", text)

    def test_menu_with_visible_children_gets_no_such_note(self):
        # 子项渲染得出来时不该多这一句——否则每个正常菜单都要付这行字节。
        item = _TreeNode(role="menu item", name="Export As...", w=40, h=10)
        menu = _TreeNode(role="menu", name="File", w=40, h=20, kids=(item,))
        root = _TreeNode(role="frame", name="GIMP", w=100, h=100, kids=(menu,))

        _records, lines = runtime.render_tree(root, None, [0], prune=True)
        text = "\n".join(lines)

        self.assertIn("Export As...", text)
        self.assertNotIn("not listed", text)


class AppNotFoundListsWindowsTests(AtspiPatchedTestCase):
    """appNotFound 的候选表要连**窗口标题**一起给。

    轨迹证据：GIMP 那一段里 agent 三次拿对话框的名字当应用名去调——
    appNotFound("script-fu") 两次、appNotFound("file-jpeg") 一次。
    它并没有猜错：屏幕上确实摆着一个 "Script-Fu Console"、一个
    "Export Image as JPEG"。错的是它无从知道这两个窗口都归 gimp 管，
    因为候选表里只有一行光秃秃的 "gimp"。
    """

    def test_listing_names_the_windows_each_app_owns(self):
        window = _NamedNode(name="Export Image as JPEG", role="frame")
        app = _NamedNode(name="gimp", role="application", children=[window])
        # node_pid 直接取 get_process_id，假节点没有这个方法就会抛
        # AttributeError，而候选表的构造整个包在 try 里——异常会把它清空，
        # 测试于是看到一条光秃秃的 appNotFound，查半天才发现是假节点的锅。
        app.get_process_id = lambda: 0

        with mock.patch.object(runtime, "iter_apps", return_value=[app]), \
             mock.patch.object(runtime, "app_windows",
                               return_value=[(0, window)]), \
             mock.patch.object(runtime, "matches_query", return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                runtime.resolve_app("file-jpeg")

        message = str(caught.exception)
        self.assertIn('appNotFound("file-jpeg")', message)
        # 光有 "gimp" 不够——要能看出那个对话框归它管。
        self.assertIn("gimp", message)
        self.assertIn("Export Image as JPEG", message)


class VisibleCellsIndexerTests(AtspiPatchedTestCase):
    """render_visible_cells 必须拿到 indexer——漏传时它整条路径都会崩。

    出处是第 108 题（LibreOffice Calc 算年龄）的真实轨迹：get_app_state
    连着两次返回 `name 'indexer' is not defined`，agent 只好整题改用 Bash。

    根因是作用域：render_visible_cells 是**模块级函数**，看不到 render_tree
    的局部变量 indexer，而它函数体里直接用了这个名字。Python 直到真正执行到
    那一行才报 NameError，所以静态上毫无征兆——而那一行正是 LibreOffice Calc
    表格的渲染路径（自管理容器拒绝枚举后的替代路径）。
    """

    def test_signature_accepts_indexer(self):
        import inspect
        params = inspect.signature(runtime.render_visible_cells).parameters
        self.assertIn("indexer", params,
                      "render_visible_cells 必须显式接收 indexer，"
                      "否则函数体里那个自由名字会在运行时炸")

    def test_call_site_passes_indexer(self):
        # 光有参数不够：调用点漏传的话，默认值 None 会让单元格退回遍历序号，
        # 下标就不再跨快照稳定了——那是另一种更隐蔽的错。
        import inspect
        source = inspect.getsource(runtime.render_tree)
        self.assertIn("render_visible_cells(", source)
        call = source[source.index("render_visible_cells("):]
        call = call[:call.index(")\n")]
        self.assertIn("indexer=indexer", call,
                      "render_tree 调用 render_visible_cells 时必须把 indexer 传下去")
