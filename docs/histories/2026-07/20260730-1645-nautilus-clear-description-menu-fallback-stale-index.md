## [2026-07-30 16:45] | Task: Nautilus 通关——补 description、右键菜单回落、拒绝陈旧下标

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44 + Nautilus 42.6）`

### 📥 User Query
> 先修掉 把一个应用通关 再去继续推进

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/runtime.py`（元素渲染、二级动作、元素解析）、
`main.go`（记录字段）、`runtime_test.py`（+11 条回归测试）。

**Key Actions:**
- **渲染 AT-SPI `description`**: 新增 `node_description()`，作为独立的
  ` Description: …` 段渲染；裁剪判据与"结构性填充"判据都把它算作可识别信号。
- **`perform_secondary_action` 增加合成回落**: 开菜单类动作调用后校验菜单是否
  真的弹出，没弹出就合成右键点击，并如实标注通道。新增
  `CONTEXT_MENU_ACTIONS`、`context_menu_visible()`、`MENU_SETTLE_SECONDS`。
- **`find_element` 校验路径解析结果**: 新增 `record_still_matches()`，
  `runtimeId` 解析出的节点身份对不上时不再直接返回，改走按身份搜索。

### 🧠 Design Intent (Why)

#### 一、名字为空的按钮：可读标识全在 description 里

实测 Nautilus 工具栏，AT-SPI 真值：

```
push button   name=''      desc='Go back'
push button   name=''      desc='Go forward'
toggle button name=''      desc='Search'
push button   name=''      desc='Show list'
toggle button name='Menu'  desc='Show operations'
toggle button name='Menu'  desc='View options'
```

**四个按钮名字全空，三个按钮名字完全相同。** 只渲染 name 的话，"返回"这种
文件管理器核心操作在树里就是一个无名 `push button`，agent 除了按像素坐标猜
没有别的办法——a11y 优先的路径在这里直接断掉。

macOS 与 Windows 侧都只取 name，所以这不是"移植时漏了"：GTK 的惯例与
AXTitle/UIA Name 相反，可读标签常常只填在 tooltip（即 AT-SPI description）里。
**Linux 侧必须多读一处。**

选择与 name 分开渲染而不是顶替它：name 是元素身份的一部分（`record-trajectory.py`
的轨迹回放、`evaluate-pruning.py` 的保留率都按 `role + name` 匹配），
改写它会让同一个元素在不同版本间对不上号。

意外收获：侧边栏条目的 description 是**目标路径**（`/home/user/Documents`），
比标签 `Documents` 更适合导航判断。代价 +5% token（984 → 1038）。

#### 二、`menu` 动作永远返回成功、永远不开菜单

Nautilus 文件图标带 `open` / `menu` 两个动作。逐条排除：

```
未选中直接 do_action(menu)              -> True，菜单项 0 个
grab_focus 后 do_action(menu)           -> True，菜单项 0 个
Selection.select_child 后 do_action     -> True，菜单项 0 个
真实右键点击                             -> 菜单打开，13 个菜单项
```

**与焦点、选中状态都无关，这个动作就是个谎。** 而右键弹出的菜单本身
a11y 完全可读（独立顶层 `window` + `menu` + 13 个 `menu item`，约 200 token），
`main_window()` 也能正确选中它——语义**观测**没问题，坏的只有语义**执行**。

这正是"a11y 优先、必要时回落合成"该处理的情形，所以做成自动回落而不是报错让
模型自己想办法：动作返回了 True，模型没有任何办法知道它没生效。

**回落只对开菜单类动作开放**（`CONTEXT_MENU_ACTIONS`）。开菜单是幂等的，
重复一次没有副作用；而 `Move to Trash` 这类动作可能已经生效只是观测不到，
自动重试等于执行两次——同一份右键菜单里，`Rename…` 上面第二项就是
`Move to Trash`。

#### 三、陈旧的 element_index 会静默操作到别的控件

`runtimeId` 是一条子节点下标路径，**位置性**的。旧实现解析成功就直接返回，
不校验身份。实测：拿右键菜单打开时的快照（`9 menu item Rename…`），
菜单关掉之后再用 index 9，同一条路径解析到的是工具栏的
`toggle button Menu (View options)`——"重命名"变成了"切换视图选项"，
而且一路 `isError=False`，从记录上完全看不出来。

这是最坏的失败模式：**不可检测，且可能是破坏性的**。判据按快照里实际有的
标识逐级收紧（automationId > role+name > role+位置），对不上就落回按身份搜索，
搜不到就干脆失败——宁可让调用方重新取一次状态。

### ✅ Verification

**Nautilus 六项核心操作全部走通，每项都用外部真值验收（不采信工具自己的返回）：**

| 操作 | 通道 | 外部验收 |
|---|---|---|
| 读状态（侧边栏/文件/工具栏） | — | 11 个侧边栏条目 + 3 个文件全部可寻址 |
| 打开右键菜单 | `[semantic]` 失效 → `[synthesis]` 回落 | 13 个菜单项出现在树里 |
| 重命名 alpha.txt → renamed.txt | `[semantic]`（写入并回读确认） | **文件系统**：`renamed.txt` 存在 |
| 新建文件夹 reports | `[semantic]` | **文件系统**：`reports/` 存在 |
| 模态对话框可见可操作 | — | `Window: "New Folder"`，6 节点 ≈110 token |
| 侧边栏导航到 Documents | `auto` → `[synthesis]` 回落 | **wmctrl**：窗口标题变为 `Documents` |

侧边栏条目**没有 Action 接口**（只有 component），`click_method:"accessibility"`
明确报错而不是静默失败，`auto` 正确回落到坐标点击——这是 Nautilus 自身的
a11y 缺口，不是本项目的缺陷，行为符合预期。

**回归测试 +11 条，全部先在改动前的代码上确认会失败。** 其中
`test_find_element_refuses_stale_path_end_to_end` 在旧代码上返回的是**错误的控件**
而不是 None，正是上面第三条描述的缺陷。`./scripts/ci.sh` 全绿，87 个 Python 单测通过。

### 📌 Notes
- **截图会关掉右键菜单**：GTK 菜单持有指针/键盘 grab，`import`/截图工具向 X
  抓取时菜单立即关闭。这条对双轨道设计有直接影响——VLM 轨道观测**瞬态弹层**时
  会破坏它要观测的状态，而 a11y 读取不抓取、不干扰。已记入待办 #28。
- 排查过程中我一度把"右键没反应"归因于应用，实际是我把**窗口相对坐标**当成了
  屏幕坐标（树里的 Frame 相对窗口原点，实测差值恰好等于 frame 原点 (94,54)）。
  另一次把"Rename 没生效"归因于动作失效，实际弹层就在树里、只是被输出截断挡住了。
  两次都是判据取错，不是被测对象的问题。
- `MENU_SETTLE_SECONDS` 可用 `OPEN_COMPUTER_USE_MENU_SETTLE` 覆盖（默认 0.6s）。
