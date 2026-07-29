## [2026-07-29 23:28] | Task: 修复 Linux 输入链路的两处静默失败

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44）`

### 📥 User Query
> 编译 Linux MCP，实测这条链路有什么问题；确认问题后把修复写了，并且多补测试、多做验证。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux`（`runtime.py` 的可编辑控件选择与输入合成路径、MCP server instructions），以及仓库级测试与验证脚本。

**Key Actions:**
- **可编辑控件选择**: `find_editable_text()` 不再用 `find_first()` 取树序第一个带 `EditableText` 接口的节点，改为要求 `EDITABLE` 状态并按 `FOCUSED` > `SHOWING` > 其它排序。
- **写入结果校验**: `insert_text()` 在调用 `Atspi.EditableText.insert_text()` 之后回读字符数，没增长就返回 `False`，让调用方退化到键盘合成，而不是把假成功当成功；空文本直接按 no-op 成功返回，不再产生无意义的写入调用。
- **输入合成焦点守卫**: 新增 `window_is_active()` / `focus_window()` / `require_window_focus()`。`press_key`、`scroll`、`drag`、coordinate `click`，以及 `type_text` 退化到合成时，都会先确认或夺取目标窗口焦点，抬窗失败则抛错。`focus_window()` 对窗口内 `FOCUSABLE` 子控件抓焦点并优先试仍带 `FOCUSED` 状态的那个，尝试次数由 `FOCUS_GRAB_CANDIDATES` 封顶。
- **协议面文档**: MCP `serverInstructions` 补充说明 Linux 输入合成是全局的、动作工具会抢前台，并提示需要避免抢焦点时改用 `set_value` 或 element-targeted `click`。
- **测试**: 新增 `apps/OpenComputerUseLinux/runtime_test.py`（28 个用例，假 AT-SPI 节点驱动，无需桌面会话），新增 5 个 Go 断言测试守住嵌入的 `runtime.py` 不回退；`scripts/ci.sh` 接入 `py_compile` 与 Python 单测（缺 Atspi typelib 时跳过）。
- **端到端验证脚本**: 新增 `scripts/verify-linux-input-chain.py`，拉起真实 MCP server 对真实应用做动作，再直接读 AT-SPI 真值比对，能自动清理自己写入的探针文本。

### 🧠 Design Intent (Why)
*两个 bug 的共同形态是"工具返回 `isError=false`，实际什么都没发生"。动作类工具成功后返回的是一棵新的 accessibility tree，看起来像执行确认，其实只是快照，没有任何 before/after 比对，因此静默失败会被完整地伪装成成功——对 agent 来说这比直接报错危险得多。*

*第一处：GTK app 里树序靠前的往往是隐藏占位控件，它实现了 `EditableText` 接口却没有 `EDITABLE` 状态。对它调 `insert_text()` **返回 `True`** 但字符数不变，文本凭空消失。只看接口存不存在不足以定位目标，必须叠加状态判断；而且既然 AT-SPI 的返回值本身不可信，就只能回读字符数来确认。*

*第二处：`Atspi.generate_keyboard_event` / `generate_mouse_event` 走 XTEST 全局合成，按键落到当前输入焦点窗口、点击落到该坐标最上层窗口，与 tool call 里的 `app` 参数完全无关。实测中目标是 gedit、焦点在终端，所有按键都进了终端而工具全部报成功。最坏情况是本该输入编辑器的文本落进 shell 并被回车执行，所以这里宁可硬失败也不能静默投递。macOS 侧用 `CGEvent.postToPid` 做进程定向投递，Linux 没有等价能力，只能用"先夺焦点，失败就拒绝"换取同等安全性。frame 自身的 `grab_focus()` 在 GTK 上恒返回 `False`，必须落到窗口内的 `FOCUSABLE` 子控件；抓焦点会真的移动应用内焦点，所以候选数量必须封顶。*

*`type_text` 的 AT-SPI 直写路径不需要窗口焦点，因此守卫只加在退化分支上，避免无谓地抢用户的窗口。*

### ✅ Verification
在 Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44 上，针对 gedit 实测。

- `(cd apps/OpenComputerUseLinux && go test ./...)` passes；`go vet` / `gofmt -l` clean。
- `(cd apps/OpenComputerUseLinux && python3 -m unittest runtime_test)` passes（28 用例）。
- `python3 -m py_compile apps/OpenComputerUseLinux/runtime.py` passes。
- `scripts/check-docs.sh`、`scripts/check-action-pinning.sh` passes。`scripts/check-repo-hygiene.sh` 在改动前后同样失败（缺 `.editorconfig` 等模板文件），属于既有问题，本次未处理。
- **回归测试有效性**：两个 Python 回归用例与 4 个新增 Go 用例在改动前的 `runtime.py` 上全部失败、改动后全部通过。
- **端到端**：`scripts/verify-linux-input-chain.py --app gedit` 对改动前构建报 2 项 FAIL（`type_text` 文本未落到焦点控件、目标窗口未激活时 `press_key` 静默误投），对改动后构建 3 项全 PASS（`type_text` 字符数 31 → 47，`press_key` 先把目标窗口抬到前台再合成），且探针文本被自动清理、缓冲区精确还原。
- 判定一律用直接读取 AT-SPI 的 `get_character_count` / `get_text` / `ACTIVE` 状态，而不是采信工具自己返回的 accessibility tree。

### ⏭ Follow-ups
本轮（2026-07-29 23:28）识别出的三项遗留，已在下面的续做里全部处理完。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
- `apps/OpenComputerUseLinux/runtime_test.py`（新增）
- `apps/OpenComputerUseLinux/main.go`
- `apps/OpenComputerUseLinux/main_test.go`
- `scripts/verify-linux-input-chain.py`（新增）
- `scripts/ci.sh`
- `docs/ARCHITECTURE.md`

---

## [2026-07-30 00:20] | Task: 续做——补掉上一轮留下的三项遗留

### 📥 User Query
> 你去看看那三条 follow-up，然后修一下吧。

### 🛠 Changes Overview
**Scope:** 同上，`apps/OpenComputerUseLinux` 与仓库级测试/验证脚本。

**Key Actions:**
- **动作结果如实说明执行路径**: `perform_operation()` 为每条分支收集 `notes` 并随响应返回，Go 侧 `resultWithNotes()` 把它们渲染在 accessibility tree 前面。AT-SPI 语义动作和直写标注为已确认；坐标点击、按键合成、拖拽、滚动一律带上 `UNVERIFIED_SYNTHESIS` 说明。
- **无变化检测（零额外开销）**: Go `actionResult()` 用动作前**已经缓存**的快照和动作后的新快照比对窗口标题、tree、焦点、选区，完全相同就追加"什么都没变"的提示。截图排除在比较之外。
- **type_text 改为在 caret 处插入**: 新增 `text_insertion_point()`，按"非空选区起点 > caret > 末尾追加"决定写入点；有选区时先 `delete_text()` 再插入，与真实打字一致。`insert_text_detail()` 返回 `(ok, before, after)` 供 notes 如实汇报字符数。
- **set_value 回读确认**: `set_element_value()` 不再直接相信 `set_text_contents()` 的返回值，改为回读比对（等于目标值、或至少发生变化都算成功）。
- **过滤 INT_MIN 哨兵坐标**: `extents()` 增加原点检查，新增 `MAX_SANE_EXTENT` 常量与尺寸检查共用。
- **参数校验前置于夺焦点**: 抽出 `parse_key()`，`press_key` 先解析按键再 `require_window_focus()`。夺焦点是有副作用的（会打断用户），不该为一个拼错的按键先把窗口抢过来再报参数错误。
- **测试**: Python 单测 28 → 53 个，新增 `ExtentsTests`、`TextInsertionPointTests`、`CaretInsertionTests`、`SetElementValueTests`、`ActionNotesTests`；Go 新增 5 个用例（含 `observablyChanged` / `resultWithNotes` 的直接单测）。验证脚本从 3 项检查扩到 7 项。`scripts/ci.sh` 增加对 `apps/OpenComputerUseLinux/tests/` 和验证脚本的语法检查——那些是需要真实桌面的集成测试，CI 跑不了，但不该悄悄烂掉。

### 🧠 Design Intent (Why)
*`insert_text` 一直追加到末尾，等于无视 agent 刚刚用 `click` 放好的光标位置——工具描述写的是"type literal text"，用户和 agent 的直觉都是"在光标处输入"。有选区时先删再插也是同一个道理：真的敲键盘就会覆盖选中内容。只有控件不支持 caret 查询时才退回追加。*

*`set_text_contents()` 和 `insert_text()` 是同一类 API，同样会对写不进去的控件返回 `True`（实测中对某个 Search 控件调用返回成功但字符数始终为 0），所以回读确认必须一起补上。判定放宽成"等于目标值或至少变了"，是为了容忍会规范化输入的控件，同时仍能抓住"纹丝不动"这种明确失败。*

*变化检测这里最初担心的是成本：完整 `render_tree()` 实测 ~360ms，而截图只要 ~30ms，动作前再遍历一次树会让耗时近乎翻倍。后来发现根本不用付这个成本——动作工具契约要求先调 `get_app_state`，Go 侧本来就缓存着动作前的快照，直接拿来比对即可。截图必须排除，否则光标闪烁会让每次动作都"有变化"，信号立刻失效。*

*执行路径上报解决的是最初那个根本问题：动作成功后返回一棵新的 accessibility tree，读起来像执行确认，其实只是快照。现在 agent 能直接区分"已确认写入"和"best-effort 合成、未确认"。*

### ✅ Verification
- `go vet` / `gofmt -l` clean；`go test ./...` passes。
- `python3 -m unittest runtime_test` passes（52 用例，上一轮 28）。
- **回归有效性**：18 个新增 Python 用例在本轮改动前的 `runtime.py` 上全部命中（5 个断言失败 + 13 个功能缺失），改动后全部通过；在旧代码上仍通过的 6 个正是断言"不该被破坏"的行为（正常坐标、副屏负坐标、无 caret 时追加等）。4 个新增 Go 断言同样在改动前失败。
- **端到端**：`scripts/verify-linux-input-chain.py --app gedit` 对改动前构建 3 项 FAIL（INT_MIN 哨兵坐标 228 行、caret 处插入退化成末尾追加、合成动作未标注未确认），对改动后构建 7 项全 PASS。
- 验证脚本自身也修了两处会互相污染的设计：改用 `Home` 而不是 `ctrl+a` 做焦点检查，并在 `type_text` 类检查前先收起选区——否则残留的全选会让下一次 `type_text` 走"替换选区"分支，断言失去意义。

### ⚠️ 已知代价
抢焦点在共享桌面上是能被用户直接感知的：验证脚本连续执行多个动作时会反复把目标窗口抬到前台，期间用户的击键会落进目标应用。这是 Linux 上"确保送达"的必然代价，也是与 macOS `CGEvent.postToPid` 非侵入路线的有意分歧，已记入 `docs/ARCHITECTURE.md`。

### 🔀 与 `feat/linux-runtime-fixes` 的整合
本轮改动 rebase 到 `feat/linux-runtime-fixes`（遍历安全、点击层级、send_key 加固共 7 个 commit）之上，git 层面无冲突。两边的重叠面与实测结论：

- 双方都改了 `extents()`：对方新增的表格坐标寻址直接调 `Atspi.Component.get_extents`，不经过 `extents()` 助手，因此我的 INT_MIN 过滤与之互不影响。
- `send_key` 合并后与对方分支逐字节一致，本轮只是把解析部分抽成 `parse_key()` 并保留全部原注释与语义。
- 对方把 `Secondary Actions` 改名为 `More actions` 并过滤掉 click 自身的动作；本轮新增的 notes、验证脚本、测试均未引用旧措辞。对方的 `click` 描述已写明"坐标点击会抢焦点"，与本轮补充的 `serverInstructions` 相互印证，无矛盾。
- 合并后实测同一棵树里两边修复同时生效：INT_MIN 坐标 0 处（本轮），`Secondary Actions: click` 0 处、容器守卫提示 5 处（对方）。
- 对方的 `tests/test_send_key_linux.py` 在合并后的构建上，于全新 Writer 实例上连续两次 PASS（`ctrl+a` 产生真实选区、`Return` 段落数 1→2、拼错修饰键被拒绝）。首次运行曾报 `Return` 未换行，复查为 Writer 实例状态残留 + `wmctrl` 激活竞态所致：用同一套步骤跑对方分支原样构建也会复现，换全新实例后两个构建都通过，非本轮回归。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
- `apps/OpenComputerUseLinux/runtime_test.py`
- `apps/OpenComputerUseLinux/main.go`
- `apps/OpenComputerUseLinux/main_test.go`
- `scripts/verify-linux-input-chain.py`
- `docs/ARCHITECTURE.md`
