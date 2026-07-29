## [2026-07-30 01:21] | Task: 修复"应用活着但 a11y 是空壳"被当成正常状态

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44）`

### 📥 User Query
> 按 `docs/exec-plans/active/20260730-linux-a11y-first-osworld.md` 推进 P0 可靠性攻坚。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux`（`runtime.py` 的快照诊断、`main.go` 的 get_app_state 返回）。

**Key Actions:**
- **撤销一个错误诊断**: plan 里原有的"僵尸 AT-SPI 注册"经严格复现证实不存在，已在里程碑和决策记录中撤销并说明原委。
- **新增 `snapshot_diagnostics()`**: 当整棵 accessibility tree 里除窗口框外没有任何带名字、带动作或带值的节点时，返回一条可执行的诊断，说明这是 a11y 不可用而非界面为空，并给出常见成因（Chromium/Electron 需 `--force-renderer-accessibility`；snap 封装接不上 a11y 总线）。
- **`get_app_state` 打通 notes 通路**: `perform_operation` 的 `get_app_state` 分支返回 `notes`，Go 侧 `snapshotState` 由 `snapshot.result()` 改为 `snapshot.resultWithNotes(notes)`。此前 notes 只有动作工具能带。

### 🧠 Design Intent (Why)
*先说撤销的那条。原判断是"应用退出后 AT-SPI 仍残留注册"，来源是观察到 Chrome 在 AT-SPI 里只有 2 个节点、而 `ps -eo args | grep -c "[g]oogle-chrome"` 返回 0。但 Chrome 的真实进程名是 `/opt/google/chrome/chrome`，那个 grep 匹配不到，Chrome 当时其实活着（11 个进程）。严格复现（gedit / VLC × SIGKILL / SIGTERM）显示 AT-SPI 在进程死后 2 秒内即干净注销，僵尸注册并不存在。*

*但现象是真的，根因是另一回事：**应用活着、窗口正常、标题正常，a11y 树里却只有一个窗口框**。此时 `get_app_state` 返回 `isError=false`，agent 拿到的是"成功 + 一个空窗口"，无从分辨"这个界面本来就是空的"和"我根本看不见这个界面"。它会在一个自己看不见的应用上反复试错，而正确动作其实是换 a11y 启动参数或切到截图通道。*

*这与本项目已修的几个 bug 属于同一类：**返回值说成功，内容其实是空的**。macOS 侧有明确先例——`docs/ARCHITECTURE.md` 记载 macOS 在找不到可用窗口时返回明确错误，而不是"把 application 根节点或无截图窗口伪装成可操作状态"。这里是同一原则在 Linux 上的落地。*

*选择给诊断而不是报错：窗口确实可能合法地为空（空白对话框），报错会误伤；而诊断既能让 agent 分辨两种情况，又正好充当 plan 里定义的"切到 VLM 通道"的信号之一。*

### ✅ Verification
- `gofmt -l` clean；`go vet ./...`、`go test ./...` passes。
- `python3 -m unittest runtime_test` passes（59 用例，上一轮 53）。
- **回归有效性**：新增的 6 个用例在改动前的 `runtime.py` 上全部报错（函数不存在），改动后全部通过。
- **真实环境正例**：Chrome 未加 `--force-renderer-accessibility` 时有 11 个活进程、AT-SPI 注册正常、窗口标题为 `about:blank - Google Chrome`，但树里只有 1 个元素。修复后返回的首行即为诊断提示。
- **真实环境反例（无误报）**：gedit 241 元素、gnome-terminal 50 元素、Chrome 加上 `--force-renderer-accessibility` 后 210 元素，三者均不触发诊断。
- `scripts/verify-linux-input-chain.py --app gedit` 7 项全 PASS。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
- `apps/OpenComputerUseLinux/runtime_test.py`
- `apps/OpenComputerUseLinux/main.go`
- `docs/exec-plans/active/20260730-linux-a11y-first-osworld.md`
