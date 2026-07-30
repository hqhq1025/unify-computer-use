## [2026-07-30 10:40] | Task: 修复模态对话框对 agent 完全不可见

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44 + LibreOffice 7.3）`

### 📥 User Query
> 按 execution plan 推进待办 #1：实测 LibreOffice 的「菜单 → 对话框」链路能否走纯语义调用。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/runtime.py` 的顶层窗口选择。

**Key Actions:**
- **`main_window()` 增加模态优先**: 顺序由 `ACTIVE > SHOWING > 第一个` 改为
  `可见模态对话框 > ACTIVE > SHOWING > 第一个`。要求同时具备 `MODAL` 与 `SHOWING`，
  避免已关闭但仍挂在树上的对话框抢走主窗口。

### 🧠 Design Intent (Why)
*实测 LibreOffice Writer 打开「格式 → 段落」对话框后，两个顶层窗口的状态是：*

```
frame  "Untitled 1 - LibreOffice Writer"   SHOWING, VISIBLE
dialog "Paragraph"                          SHOWING, VISIBLE, MODAL
```

***两者都不上报 ACTIVE。** 旧逻辑第一轮按 ACTIVE 筛选一无所获，第二轮按 SHOWING
筛选时 frame 因为在子节点顺序里排在前面而胜出。结果是只要弹出模态对话框，
`get_app_state` 返回的就是主窗口的树，树里连一个 dialog 角色的节点都没有——
**agent 对对话框完全不可见**。*

*这条影响面很大：抽样 OSWorld 8 个 domain 的真实任务后，主导操作类型正是
「菜单导航 → 对话框交互」（Writer 改行距、Impress 改文本色、VLC 改偏好设置、
GIMP 转色彩模式都是这个形状）。对话框看不见，等于这一整类任务都做不了。*

*macOS 侧可以直接读 `kAXFocusedWindowAttribute` 问应用哪个窗口有焦点，
AT-SPI 没有等价属性，只能从状态推断，所以不能照搬。改用 `MODAL` 作为判据是
更强的语义：模态按定义阻塞应用其余部分的交互，它就是此刻唯一可操作的窗口——
这比 ACTIVE 更可靠，尤其是在 ACTIVE 本身就不可信的应用上。*

*把模态排在 ACTIVE **之前**而不是之后，是因为失效场景恰恰是 ACTIVE 不可信的时候；
若排在后面，遇到 frame 谎报 ACTIVE 就又会瞎掉。*

### ✅ Verification
- `gofmt -l` clean；`go vet ./...`、`go test ./...` passes。
- `python3 -m unittest runtime_test` passes（64 用例，上一轮 59）。
- **回归有效性**：新增 5 个用例，其中 2 个在修复前的代码上失败（模态优先相关），
  3 个断言未被破坏的既有行为（ACTIVE/SHOWING 回退、隐藏的陈旧模态框不抢窗口、无窗口时报错）。
- **真实环境**：Paragraph 对话框打开状态下，
  - 修复前 `get_app_state` 返回 `Window: "Untitled 1 - LibreOffice Writer"`，
    树里 dialog 角色节点 **0 个**，找不到任何行距控件
  - 修复后返回 `Window: "Paragraph"`，根节点为 `0 dialog Paragraph`，
    共 165 个元素，`panel Line Spacing` / `label Line Spacing` 均可见
- **链路本身**：`格式 → 段落` 两步均为纯 `element_index` 语义调用
  （`click_method=accessibility`），全程无坐标，对话框成功打开。

### ⚠️ 同批实测发现、尚未处理
- **命名歧义**：子串匹配 `Format` 会同时命中 `menu Format`、
  `check menu item Formatting Marks`、`menu Formatting Mark`、
  `menu item Clone Formatting`。agent 面临同样的消歧成本，
  需要角色 + 精确名匹配才能可靠定位。
- **菜单展开状态不可见**：点开菜单后 `EXPANDED` 状态为空，
  agent 无法从状态判断菜单是否真的展开，只能靠重读树发现多了菜单项。
- **对话框不上报 ACTIVE**：见上，已用 MODAL 绕过，但说明该应用的窗口状态整体不可信。

### 📁 Files Modified
- `apps/OpenComputerUseLinux/runtime.py`
- `apps/OpenComputerUseLinux/runtime_test.py`
