## [2026-07-29 15:35] | Task: 厘清 Linux 侧点击工具的层级，停止把 click 自身的动作列为"次要动作"

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64 容器（Ubuntu 22.04 + Xvfb X11 + XFCE + at-spi2-core 2.44 + LibreOffice 7.3）`

### 📥 User Query
> 观察到 agent 大量使用坐标点击而不走语义动作。`perform_secondary_action` 应该是一等公民、是捷径才对，重新安排层级，参考 macOS 的实现。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/runtime.py` 的 `action_names()` 与快照渲染，`main.go` 的 `click` / `perform_secondary_action` tool description。

**Key Actions:**
- **过滤已被 click 覆盖的动作**: 新增 `CLICK_COVERED_ACTIONS`，与 `preferred_action_index()` 的 `preferred_exact` 同源；`action_names()` 不再列出这些动作。
- **措辞**: 树里的 `Secondary Actions:` 改为 `More actions:`，且只在确实存在额外动作时出现。
- **tool description 写明行为差异**: `click` 明确"优先 `element_index`（调用元素自身的 accessibility action，可靠且**不抢焦点**），`x`/`y` 仅在树里找不到目标时兜底（合成真实鼠标事件，**会抢焦点**）"；`perform_secondary_action` 改述为"在 `click` 已执行的默认动作之外调用额外动作（increment / decrement / expand），动作名取自树里的 `More actions`"。

### 🧠 Design Intent (Why)
*Linux 侧的 `click(element_index)` 本来就走语义调用——`preferred_action_index()` 会挑中 `click` / `press` / `activate` 之一再 `do_action()`，与 macOS 侧 `click` 依次尝试 `AXPress` / `AXConfirm` / `AXOpen` / `AXShowMenu`（`ComputerUseService.swift:865-877`）是同一套逻辑。能力本身没缺，缺的是**呈现**。*

*macOS 的 `meaningfulActions()`（`AccessibilitySnapshot.swift:1754`）会把 `AXPress` / `AXShowDefaultUI` / `AXShowAlternateUI` / `AXConfirm` / `AXScrollToVisible` 从展示列表里过滤掉，因为它们已经被 `click` 覆盖了。所以 macOS 上 "secondary" 名副其实，指的是 increment / decrement 这类剩余动作。*

*Linux 移植时漏了这一层。AT-SPI 里节点的默认动作名恰好就叫 `click`，于是每个可点击节点都渲染出 `Secondary Actions: click`——实测一次 `get_app_state` 里这句出现 1000+ 次。模型读到"还有个次要动作叫 click"，自然怀疑 `element_index` 这条路不够用，在两个工具之间摇摆，最后退回坐标点击。而坐标点击走的是 `generate_mouse_event`，会真的抢焦点，与 index 点击行为天差地别——原 description 把两者完全并列，一个字都没提这个差异。*

*行为基线（修复前，一道 Calc 填充任务）：12 次 `click` 中 7 次用坐标、5 次用 `element_index`，即 58% 走了抢焦点的兜底路径。*

### ✅ Verification
LibreOffice Calc，配额 1200：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| `Secondary Actions` 行数 | 1000+ | 0 |
| `More actions` 行数 | — | 2 |
| 快照字符数 | 67019 | 58334（−13%） |

改后仅存的 `More actions` 是 `incrementLine` / `decrementLine` / `incrementBlock` / `decrementBlock`，全部来自滚动条——名副其实的额外动作。

### ⚠️ Known Limitations
`CLICK_COVERED_ACTIONS` 与 `preferred_action_index()` 的 `preferred_exact` 是两份需要手工保持同步的常量。将来若扩展默认动作集合，两处必须同改，否则会重新出现"列出了 click 自己会调的动作"或"隐藏了实际调不到的动作"。
