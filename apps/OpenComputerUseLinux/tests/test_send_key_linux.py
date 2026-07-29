"""press_key 修复的回归验证。

判据不看截图、不看窗口标题，直接读 AT-SPI 的文本内容与选区范围——
这两个是应用内部状态，伪造不了。

对照（修复前的已知错误行为）：
  ctrl+a  -> keysym 65507 被截断成 227，修饰键根本没按下，
             且 'a' 走 STRING 绕过修饰键，结果是往文档里插入字面 'a'，选区数 0
  Return  -> keysym 65293 被截断成 13，实际发出 '4'，不换行
"""
import subprocess
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

OCU = "open-computer-use"


def call(calls):
    p = subprocess.run(
        [OCU, "call", "--calls", calls],
        capture_output=True, text=True, timeout=180,
    )
    return p.returncode, p.stdout, p.stderr


def writer_text_iface():
    d = Atspi.get_desktop(0)
    found = []

    def walk(n, dep=0):
        if dep > 14:
            return
        try:
            if n.get_role_name() == "paragraph":
                ti = n.get_text_iface()
                if ti:
                    found.append(ti)
            for i in range(n.get_child_count()):
                walk(n.get_child_at_index(i), dep + 1)
        except Exception:
            pass

    for i in range(d.get_child_count()):
        c = d.get_child_at_index(i)
        if c.get_name() == "soffice":
            walk(c)
    return found


def snapshot():
    ifaces = writer_text_iface()
    texts = [Atspi.Text.get_text(t, 0, -1) for t in ifaces]
    sels = []
    for t in ifaces:
        try:
            n = Atspi.Text.get_n_selections(t)
            sels += [Atspi.Text.get_selection(t, i) for i in range(n)]
        except Exception:
            pass
    return texts, sels


fails = []

# ---- 用例 1: ctrl+a 应当产生真实选区 ----
# 预置文本刻意走 xdotool 而不是 OCU 的 type_text：后者当前会作用于树里
# **第一个**可编辑节点而非焦点节点（见 find_editable_text），目标不确定，
# 会让本测试变成 flaky。本测试只负责验证 press_key，不该依赖另一个待修功能。
subprocess.run(["xdotool", "type", "--delay", "40", "HELLOWXYZ"], check=False)
time.sleep(2)
call('[{"tool":"get_app_state","args":{"app":"soffice","max_tree_nodes":10}},'
     '{"tool":"press_key","args":{"app":"soffice","key":"ctrl+a"}}]')
time.sleep(2)
texts, sels = snapshot()
print(f"[1] ctrl+a  文本={texts!r}  选区={sels!r}")
if any("HELLOWXYZa" in t for t in texts):
    fails.append("ctrl+a 插入了字面 'a'(STRING 绕过修饰键)")
if not sels:
    fails.append("ctrl+a 没有产生任何选区")

# ---- 用例 2: Return 应当真的换行（段落数 +1），而不是插入 '4' ----
# 先取消上一用例留下的全选，否则 Return 会替换选中内容而不是插入换行，
# 段落数不增反而可能减少 —— 那是用例间耦合导致的假阴性，不是 press_key 的问题。
subprocess.run(["xdotool", "key", "End"], check=False)
time.sleep(1)
before = len(writer_text_iface())
call('[{"tool":"get_app_state","args":{"app":"soffice","max_tree_nodes":10}},'
     '{"tool":"press_key","args":{"app":"soffice","key":"Return"}}]')
time.sleep(2)
after_ifaces = writer_text_iface()
after = len(after_ifaces)
after_texts = [Atspi.Text.get_text(t, 0, -1) for t in after_ifaces]
print(f"[2] Return  段落数 {before} -> {after}  文本={after_texts!r}")
if any("4" in t for t in after_texts):
    fails.append("Return 插入了字面 '4'(keysym 当 keycode)")
if after <= before:
    fails.append(f"Return 没有换行(段落数 {before}->{after})")

# ---- 用例 3: 未知修饰键应当报错，而不是静默退化 ----
rc, out, err = call('[{"tool":"get_app_state","args":{"app":"soffice","max_tree_nodes":10}},'
                    '{"tool":"press_key","args":{"app":"soffice","key":"shft+s"}}]')
bad_modifier_rejected = "Unsupported modifier" in (out + err)
print(f"[3] 拼错的修饰键 shft+s 被拒绝: {bad_modifier_rejected}")
if not bad_modifier_rejected:
    fails.append("拼错的修饰键被静默忽略，退化成孤立按键")

print()
if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS: press_key 修饰键与功能键均正确送达")
