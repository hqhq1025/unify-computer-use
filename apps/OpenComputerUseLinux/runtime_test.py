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


class FakeAtspi:
    """保留真的 StateType 枚举，只替换会打到真实 a11y 总线的接口调用。"""

    StateType = STATE
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
            ("scroll_element", lambda d, p: None),
            ("time", _NoSleep()),
        ):
            original = getattr(runtime, name)
            setattr(runtime, name, value)
            self.addCleanup(setattr, runtime, name, original)

    def _notes(self, op):
        return runtime.perform_operation(op).get("notes", [])

    def test_semantic_and_synthesis_are_distinguishable(self):
        original = runtime.insert_text_detail
        runtime.insert_text_detail = lambda root, text: (True, 0, 2)
        self.addCleanup(setattr, runtime, "insert_text_detail", original)

        semantic = self._notes({"tool": "type_text", "app": "x", "text": "hi"})
        self.assertTrue(semantic[0].startswith("[semantic] "))

        runtime.insert_text_detail = lambda root, text: (False, 0, 0)
        synthesis = self._notes({"tool": "type_text", "app": "x", "text": "hi"})
        self.assertTrue(synthesis[0].startswith("[synthesis] "))

    def test_every_action_path_carries_a_channel_tag(self):
        original = runtime.insert_text_detail
        runtime.insert_text_detail = lambda root, text: (False, 0, 0)
        self.addCleanup(setattr, runtime, "insert_text_detail", original)

        for op in (
            {"tool": "press_key", "app": "x", "key": "a"},
            {"tool": "type_text", "app": "x", "text": "hi"},
            {"tool": "scroll", "app": "x", "direction": "down", "pages": 1},
            {"tool": "drag", "app": "x", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            {"tool": "click", "app": "x", "click_method": "global", "x": 5, "y": 7},
        ):
            notes = self._notes(op)
            self.assertTrue(notes, "{} 没有产生 note".format(op["tool"]))
            self.assertTrue(
                notes[0].startswith(("[semantic] ", "[synthesis] ")),
                "{} 的 note 缺少通道标签: {}".format(op["tool"], notes[0][:60]),
            )

    def test_coordinate_click_nudges_toward_element_index(self):
        """走了坐标兜底就要即时纠偏，否则 agent 不知道有更好的路。"""
        notes = self._notes(
            {"tool": "click", "app": "x", "click_method": "global", "x": 5, "y": 7}
        )

        self.assertIn("prefer click(element_index=...)", notes[0])


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

        notes = runtime.perform_operation(
            {"tool": "type_text", "app": "x", "text": "hello"}
        )["notes"]

        self.assertEqual(len(notes), 1)
        self.assertIn("confirmed it landed (3 -> 8 characters)", notes[0])
        self.assertNotIn("not verified", notes[0])

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
            {"tool": "click", "app": "x", "click_method": "global", "x": 5, "y": 7}
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
                "tool": "drag",
                "app": "x",
                "from_x": 1,
                "from_y": 2,
                "to_x": 3,
                "to_y": 4,
            }
        )

        self.assertEqual(self.focus_calls, ["drag"])
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
