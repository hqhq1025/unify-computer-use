## [2026-07-30 11:13] | Task: 拆分 a11y 与 VLM 两条观测轨道

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME + at-spi2-core 2.44）`

### 📥 User Query
> 按 execution plan 推进待办 #4：观测双轨拆分，a11y track 不带截图。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux` 的快照构建与工具面。

**Key Actions:**
- **`build_snapshot()` 新增 `include_screenshot`，默认 `False`**：截图从"每次都拍"
  变成"显式索取"。`get_app_state` 与全部动作工具都不再带图。
- **新增 `get_screenshot` 工具**：VLM 轨道的唯一入口。它只渲染一层树
  （`max_tree_nodes=1`），避免为了拿一张图顺带付整棵树的钱。
- **工具描述与 `serverInstructions` 明确两轨不对等**：a11y 是主通道，
  截图是兜底，并写清触发条件（应用无 a11y 内容 / 动作报告无可观测变化 /
  任务本身依赖像素），以及"不要为了看一眼就要截图"。

### 🧠 Design Intent (Why)
*实测数据是这次改动的直接依据：gedit 单次 `get_app_state` 是 1908 文本 token
加 1014 截图 token，截图占 **35%**。而 `build_snapshot()` 原本**无条件**调用
`capture_window_png()`，`get_app_state` 和每一个动作工具的返回都带图——
等于每次调用都在同时付 a11y 和 VLM 两条轨道的钱。*

*更关键的是它会吃掉后续裁剪工作的收益：GTK 系应用约 88% 的节点不在屏幕上，
裁剪后文本能降一个数量级，那时截图占比会升到 **80% 左右**，反过来成为主要成本。
所以双轨拆分必须排在裁剪之前做，否则裁剪的收益兑现不出来。*

*选择新开一个工具而不是给 `get_app_state` 加参数：语义最清晰，agent 明确知道
自己在切轨；而且切轨频率可以直接统计，这个数据本身就是"a11y 够不够用"的度量，
对应 plan 里 S3 报告口径的第四项。*

*工具描述刻意写成不对等的措辞（"ONLY when the accessibility tree is insufficient"、
"Do not request a screenshot 'just to check'"）。把两条路并列陈述等于暗示它们等价，
而实测下来它们的成本差着一个量级。*

### ✅ Verification
- `./scripts/ci.sh` 完整通过，退出码 0。Go 用例含新增 2 项；Python 65 用例通过。
- **成本实测（gedit）**：
  - 改动前 `get_app_state` = 1908 文本 + 1014 截图 = **2922 token**
  - 改动后 `get_app_state` = **1908 token**，无 image block
  - `get_screenshot` = 15 文本 + 1014 截图 = 1029 token，仅在显式调用时产生
- **动作工具不再带图**：`type_text` / `press_key` 返回内容类型均为 `['text']`；
  `get_screenshot` 为 `['text','image']`。
- `tools/list` 现为 10 个工具（9 个与 macOS/Windows 对齐 + Linux 特有的 `get_screenshot`）。
- `scripts/verify-linux-input-chain.py --app gedit` 7 项全 PASS。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
- `apps/OpenComputerUseLinux/main.go`
- `apps/OpenComputerUseLinux/main_test.go`
