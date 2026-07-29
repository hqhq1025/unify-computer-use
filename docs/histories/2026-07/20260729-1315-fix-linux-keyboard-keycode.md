## [2026-07-29 13:15] | Task: 修复 Linux press_key 发送错误按键

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64 容器（Ubuntu 22.04 + Xvfb X11 + XFCE + at-spi2-core 2.44）`

### 📥 User Query
> 在 Linux 上用 `press_key` 发 `ctrl+a` 没有全选，反而往文档里输入了一个字面的 `a`。排查根因并修复。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux` 的 AT-SPI 键盘合成路径（`runtime.py` 的 `send_key`）。

**Key Actions:**
- **keysym → keycode**: 新增 `keycode()`，用 `Gdk.Keymap.get_default().get_entries_for_keyval()` 把键名解析成 X11 hardware keycode；`PRESS` / `RELEASE` / `PRESSRELEASE` 三种 synth type 全部改用 keycode。移除已无调用者的 `keyval()`。
- **修饰键组合**: 单字符主键仅在**没有修饰键**时才走 `KeySynthType.STRING`；带修饰键时改走 `keycode` + `PRESSRELEASE`，使其与已按下的修饰键合成组合键。
- **修饰键不再泄漏**: 修饰键的 `RELEASE` 移入 `finally`，避免主键解析失败时修饰键永久卡在按下状态。
- **未知修饰键不再静默**: `MODIFIER_KEYS` 未命中时由 `continue` 改为抛出 `Unsupported modifier`，此前会被静默忽略并降级成输入字面字符。

### 🧠 Design Intent (Why)
*AT-SPI 的 `generate_keyboard_event(keyval, keystring, synth_type)` 只有 `SYM` 模式收 keysym，`PRESS` / `RELEASE` / `PRESSRELEASE` 收的是 hardware keycode。原实现把 `Gdk.keyval_from_name()` 得到的 keysym 直接传给了 PRESS 系模式，而 keysym 远超 X 的合法 keycode 范围 (8-255)，被截断到低 8 位后落到完全不相干的键上：`Return`(65293)→13→`4`、`space`(32)→`o`、`Escape`(65307)→27→`r`、`Tab`(65289)→9→`Escape`、`Delete`(65535)→255→`XF86RFKill`、`Control_L`(65507)→227→`XF86Finance`（即修饰键从未真正按下）。*

*后果不是静默失效，而是**静默注入错误字符或触发意外动作**，比直接报错更危险——`press_key` 的 description 里举例的 `Return`、`Tab` 恰好都在受影响之列。修饰键失效还叠加了第二层问题：即使修饰键正确按下，单字符主键走 `STRING`（"插入这段文本"语义）仍会绕过修饰键状态，所以两处必须一起修。*

### ✅ Verification
在 Ubuntu 22.04 + Xvfb(X11) + at-spi2-core 2.44 容器内针对 LibreOffice Writer 实测。

**注意**：该环境上 `get_app_state` 会先因 `'Accessible' object has no attribute 'is_text'`（`libatspi` < 2.52 无 `is_text` 便捷方法）失败，导致后续动作不执行。因此端到端验证使用的构建**额外叠加了该兼容性修复**；本次改动本身与之独立。

判定用直接读取 AT-SPI 选区状态（`Atspi.Text.get_n_selections` / `get_selection`），而非用 `type_text` 间接推断——`type_text` 优先走 `insert_text()` 程序化插入，不理会选区，会给出假阴性。

- `python3 -m py_compile apps/OpenComputerUseLinux/runtime.py` passes.
- `(cd apps/OpenComputerUseLinux && go test ./...)` passes.
- `make check-docs` passes.
- `ctrl+a`：修复前文本 `HELLO` → `HELLOa`（注入字面 `a`）、选区数 `0`；修复后选区变为 `(0,21)` 全选、无多余字符。
- `Return`：修复前 `AAA` → `AAA4`（注入 `4`）且不换行；修复后段落数 `1` → `2`，真正换行且无 `4`。
- `press_key` 传入未知修饰键（如 `foo+a`）修复后返回 `Unsupported modifier: foo`，此前会静默输入字面 `a`。
- 通过 `xmodmap -pke` 交叉核对了截断映射表，`Return`→keycode 13→`4` 等推断与实测一致。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
