# OSWorld LibreOffice 三件套：任务实际需要的 GUI 操作清单

对应 `docs/exec-plans/active/20260730-linux-a11y-first-osworld.md` 待办 **#2a（L1 调研）**。
产出目的是给 **#2b（L2 逐应用打通必须操作）** 一份"该先打哪些链路"的排序依据。

创建于 2026-07-30。

---

## 1. 数据来源与统计口径

### 数据来源

| 目录 | 任务数 |
|---|---:|
| `/home/user/OSWorld/evaluation_examples/examples/libreoffice_calc/` | 47 |
| `/home/user/OSWorld/evaluation_examples/examples/libreoffice_impress/` | 47 |
| `/home/user/OSWorld/evaluation_examples/examples/libreoffice_writer/` | 23 |
| **合计** | **117** |

每个任务是一个 `<task-id>.json`，本文引用任务时统一用 **id 前 8 位**（已核实在这 117 个里唯一），
文件名是完整 uuid。三个字段被用上了：

- `instruction`：自然语言任务描述 —— 归类的主要依据
- `config`：初始环境（下载文件 / `open` 打开 / `launch` 启动 / `execute` 预置光标位置）
- `evaluator`：判分方式 —— 用来交叉验证"到底哪个状态被检查了"

`evaluator` 的交叉验证很关键，因为 instruction 常常不说清要动哪个 UI。例如 calc `6e99a1ad`
的 instruction 只说"保留两位小数"，但 evaluator 用的是 `sheet_print` 规则（比对**显示出来的字符串**
而非单元格数值），这就把它钉死成"必须改数字格式"而不是"把值四舍五入写回去"。

### 归类口径

1. **口径是"最短 GUI 路径"**：对每个任务假定一个熟练用户会怎么用鼠标键盘完成它，
   记录这条路径上出现的 UI 动作类型。同一任务里同一类操作重复 N 次只记 1 次。
2. **一个任务可以命中多个类别**，所以各类计数之和 > 任务数。
3. **只统计"必须的"**：可有可无的等价路径不计。存在多条等价路径时取控件语义最明确的一条
   （例如 Calc 排序既能用 `数据 → 排序` 对话框也能用工具栏按钮，两者都记，因为对
   a11y 打通而言这是两条要分别验证的链路）。
4. **不统计执行轨迹**。这是**基于任务描述与判分逻辑的人工归类，不是实测轨迹统计**。
   真实 agent 可能绕路。这份清单回答的是"这个应用最该先打通哪些链路"，不是"agent 实际点了什么"。
5. `SAVE`（`Ctrl+S` + 保持格式警告框）单列一类。它几乎覆盖全部任务，不参与"最高频"排序讨论，
   但它是最容易被忽略的失败点，所以必须显式列出。

### 类别定义

| 代号 | 含义 |
|---|---|
| `MENU` | 菜单栏导航，含多级子菜单（`格式 → 文字 → 删除线`） |
| `DIALOG` | 模态对话框内改值：下拉 / 勾选框 / 单选 / 数值 / 文本 / 多标签页 |
| `CELL` | Calc 单元格定位与输入（含公式栏编辑） |
| `RANGE` | Calc 区域与行列选择：拖选、名称框、行列头、Ctrl 多选 |
| `SHEETTAB` | Calc 工作表标签页：新建 / 重命名 / 复制 / 移动 |
| `WIZARD` | 多步骤向导：图表向导、数据透视表布局 |
| `TOOLBAR` | 工具栏按钮与组合框（字号、字体名、对齐、加粗、合并单元格） |
| `COLORPICK` | 颜色下拉调色板，含自定义颜色 hex 输入 |
| `SELFMT` | 选中文本/对象后套用格式（select-then-apply 模式本身） |
| `TEXTSEL` | 精确文本选择或光标定位（段落 / 词 / 单个字符） |
| `KEY` | 键盘快捷键（Ctrl+B/U/A/C/V、F4、Ctrl+Enter 等） |
| `CONTEXT` | 右键上下文菜单 |
| `SLIDENAV` | Impress 幻灯片面板导航与选中 |
| `CANVASOBJ` | Impress 画布对象选中（文本框 / 图片 / 表格） |
| `DRAGGEOM` | 拖拽移动、缩放、重排（画布对象或幻灯片顺序） |
| `VIEWMODE` | 视图切换：大纲 / 备注 / 母版 / 面板显隐 |
| `SIDEBAR` | 侧边栏面板（幻灯片切换、版式） |
| `FILEDLG` | 文件选择 / 另存 / 导出对话框（含格式过滤器 + 保持格式警告） |
| `APPOPT` | `工具 → 选项` 全局设置树 |
| `VISUAL` | 必须靠看图才能决定做什么（a11y 树里没有答案） |
| `SAVE` | `Ctrl+S` 并处理"保持当前格式"警告框 |
| `INFEASIBLE` | 任务本身被标为不可完成，正确行为是拒答 |

---

## 2. LibreOffice Calc（47 个任务）

| 排名 | 操作类型 | 任务数 | 典型形态 | a11y 判断 |
|---:|---|---:|---|---|
| — | `SAVE` | 44/47 | 编辑完 `Ctrl+S`，xlsx 会弹"保持当前格式"警告框，不点掉就没存盘 | ✅ 可寻址（标准 `push button`，名字类似 `Use Excel 2007-365!`） |
| 1 | `MENU` | 30/47 | `插入 → 图表`、`数据 → 有效性`、`视图 → 冻结行列`、`格式 → 单元格` | ✅ 实测可寻址，但有歧义与折叠两个坑（见 §5.1） |
| 2 | `DIALOG` | 30/47 | 单元格格式对话框输格式码 `0.0" M"`；有效性对话框选"列表"再填条目 | ⚠️ 下拉提交是已知断点（见 §5.2） |
| 3 | `RANGE` | 29/47 | 选 `B1:E30`、点列头选整列、Ctrl 多选不连续的周末单元格 | ⚠️ 只有视口内的单元格在树里（见 §5.3） |
| 4 | `CELL` | 28/47 | 定位到某格，输入值或公式（VLOOKUP / DATEDIF / TEXT / SUM） | ⚠️ 同上；名称框跳转是最稳的路径 |
| 5 | `SHEETTAB` | 12/47 | 新建 `Sheet2`、重命名、复制一份放到 `Sheet 2` 前面 | ✅ 标签页是 `page tab`，右键菜单项有名字 |
| 6 | `WIZARD` | 10/47 | 图表向导 4 步；数据透视表布局要**把字段按钮拖进行/列/数据区** | ❌ 透视表布局的拖拽是硬缺口（见 §5.4） |
| 7 | `KEY` | 8/47 | `Ctrl+1` 开格式对话框、`Ctrl+D` 向下填充、`Ctrl+Shift+V` 选择性粘贴 | ✅ 全局快捷键可用；但焦点必须在网格上 |
| 8 | `TOOLBAR` | 7/47 | 合并单元格、升序排序、求和、加粗 | ✅ `push button` + `toggle button`，有名字 |
| 9 | `CONTEXT` | 4/47 | 行列头右键 → 插入列 / 隐藏行；标签页右键 → 重命名 | ✅ 已在 Nautilus 上验证过同类链路（`perform_secondary_action`） |
| 10 | `COLORPICK` | 3/47 | 字体颜色 / 背景色下拉，要精确 `#00ff00`、`#ff0000`、`#0000ff` | ❌ 调色板取色是坐标动作（见 §5.2） |
| 10 | `SELFMT` | 3/47 | 选区 → 套字体色 / 背景色 | ⚠️ 依赖 `RANGE` 与 `COLORPICK` |
| 12 | `FILEDLG` | 2/47 | 另存为 CSV（选过滤器 + CSV 选项对话框）；导出 PDF | ⚠️ 文件对话框可寻址，过滤器下拉同 §5.2 |
| 13 | `APPOPT` | 1/47 | `工具 → 选项 → 语言设置` 改小数分隔符 | ✅ 选项树是 `tree table`，节点有名字 |
| 13 | `INFEASIBLE` | 1/47 | 迷你图（LibreOffice 没有这个功能） | — |

**举例追溯**

- `MENU`：`ecb0df7a`（`数据 → 有效性`，给 Pass/Fail/Held 建下拉）、`4188d3a4`（`视图 → 冻结行列`）、
  `1334ca3e`（`视图 → 缩放`，evaluator 规则 `{"type":"zoom","method":"lt","ref":260}`）
- `DIALOG`：`21df9241`（数字格式码要产出 `1.2 M` / `3.4 B`，evaluator 用 `sheet_print`）、
  `6e99a1ad`（保留两位小数，同样是 `sheet_print`）、`eb03d19a`（选择性粘贴勾"转置"）
- `RANGE`：`01b269ae`（`B1:E30` 里的空格向上填充）、`8b1ce5f2`（Ctrl 多选周末格设红底）、
  `6054afcb`（多选行头 → 隐藏行，evaluator 检查 `row_props.hidden`）
- `SHEETTAB`：`0cecd4f3`（重命名 + 复制表 + 定位到 `Sheet 2` 之前，evaluator 检查 `sheet_name`）、
  `1273e544`、`30e3e107`
- `WIZARD`：`1954cced` / `1de60575` / `535364ea`（透视表，evaluator 检查
  `pivot_props: col_fields / row_fields / data_fields / filter`）、
  `0326d92d` / `12382c62`（图表，检查 `chart_props: type / title`）
- `COLORPICK`：`8b1ce5f2`（`#ff0000` 背景）、`21ab7b40`（`#00ff00` 字色）、
  `30e3e107`（`#0000ff` 底 + 白色粗体字）

---

## 3. LibreOffice Impress（47 个任务）

| 排名 | 操作类型 | 任务数 | 典型形态 | a11y 判断 |
|---:|---|---:|---|---|
| — | `SAVE` | 43/47 | `Ctrl+S` + "保持 PowerPoint 格式"警告框 | ✅ 可寻址 |
| 1 | `SLIDENAV` | 31/47 | "第 14 页的第一个文本框"、"第 2、3、5 页的标题" | ⚠️ 面板项有名字，但页数多时要滚动（见 §5.3） |
| 2 | `CANVASOBJ` | 29/47 | 点中画布上第 N 个文本框 / 图片 / 表格 | ❌ 最大的结构性缺口（见 §5.5） |
| 3 | `MENU` | 25/47 | `幻灯片 → 幻灯片属性`、`插入 → 表格`、`插入 → 音频或视频` | ✅ 可寻址 |
| 4 | `TEXTSEL` | 24/47 | 进入文本框编辑态，选中全部或指定行 | ⚠️ 依赖 `CANVASOBJ` 先成功 |
| 5 | `DIALOG` | 23/47 | F4 位置和大小（数值 + 保持比例勾选）、幻灯片属性（纸张方向单选） | ✅ 数值框 `set_value` 已实测可用 |
| 6 | `SELFMT` | 19/47 | 选对象/文本 → 套字号、颜色、下划线、字体名 | ⚠️ 同 `CANVASOBJ` |
| 7 | `KEY` | 15/47 | `F4`、`Ctrl+B`、`Ctrl+U`、`Ctrl+A`、`Tab` 循环选对象 | ✅ 可用；`Tab` 循环是绕开画布选中问题的可行手段 |
| 8 | `COLORPICK` | 14/47 | 幻灯片背景色、字体色；`986fc832` 还要求调色板里那个叫 **"Dark Red 2"** 的具体色块 | ❌ 见 §5.2 |
| 9 | `TOOLBAR` | 13/47 | 字号组合框、字体名组合框、对齐、项目符号开关 | ⚠️ 组合框输入可行，下拉选择同 §5.2 |
| 10 | `DRAGGEOM` | 7/47 | "把标题挪到页面底部"、"图片挪到右边"、幻灯片面板里拖动重排 | ❌ AT-SPI 没有移动语义；F4 对话框是唯一的非坐标出路 |
| 10 | `VIEWMODE` | 7/47 | `视图 → 备注`、`视图 → 母版幻灯片`、`视图 → 大纲`、`视图 → 幻灯片窗格` | ✅ 勾选型菜单项，可寻址 |
| 12 | `VISUAL` | 5/47 | "含有真人照片的幻灯片"、"删掉个人信息（含图标）"、"和标题同色" | ❌ a11y 树里没有答案，必须切 VLM 轨 |
| 12 | `FILEDLG` | 5/47 | 导出 PNG、另存 pptx、插入 6 张图片、插入 mp3 | ⚠️ 同 §5.2（格式过滤器下拉） |
| 14 | `APPOPT` | 2/47 | 关演讲者控制台；自动保存改 3 分钟 | ✅ 选项树可寻址；这两个任务的 evaluator 直接读 `registrymodifications.xcu` |
| 14 | `SIDEBAR` | 2/47 | 幻灯片切换面板选"溶解"；版式面板选空白版式 | ⚠️ 侧栏 deck 有名字；面板里的效果列表是 `ValueSet`，选中同 §5.2 |
| 14 | `CONTEXT` | 2/47 | 幻灯片面板右键 → 复制幻灯片 / 新建幻灯片 | ✅ 可寻址 |

**举例追溯**

- `SLIDENAV`：`3161d64e`（"第 14 页"）、`4ed5abd0`（"第 2、3、5 页的标题"）、`edb61b14`（"最后一页"）
- `CANVASOBJ`：`04578141`（"三个文本框自上而下分别改黄红绿"——**顺序本身就是空间信息**）、
  `05dd4c1d`（"第一个文本框"）、`ac1b39ff`（第 3 页上的表格）
- `DRAGGEOM`：`15aece23`（标题挪到底部，evaluator 用 `examine_title_bottom_position`）、
  `2b94c692`（`examine_right_position`）、`9ec204e4`（复制最后两页并按 A B A' B' 重排）
- `COLORPICK`：`986fc832`（要求调色板里的 **"Dark Red 2"**，`color_tolerance: 30`）、
  `3b27600c`（所有幻灯片蓝底）、`70bca0cc`（背景色取成标题的颜色 —— 要先读出来）
- `VISUAL`：`0a211154`（"含一张或多张真人照片的幻灯片"）、`a53f80cd`（"删掉第 4 页的个人信息，含图标"）、
  `b8adbc24`（"和上一页标题相同的颜色、位置、字号"）
- `VIEWMODE`：`ac9bb6cb`（页码变红 —— 实际要进母版）、`7dbc52a6` / `841b50aa` / `8979838c`（备注视图）、
  `ef9d12bd`（恢复左侧幻灯片面板）

**特别标注 `af23762e`**：要求用 Impress 的 "Summary Slide" 功能。现代 LibreOffice 已无此菜单项，
这是一个"instruction 假定了不存在的功能"的任务，不要拿它当链路验证目标。

---

## 4. LibreOffice Writer（23 个任务）

| 排名 | 操作类型 | 任务数 | 典型形态 | a11y 判断 |
|---:|---|---:|---|---|
| — | `SAVE` | 20/23 | `Ctrl+S` + "保持 Word 格式"警告框 | ✅ 可寻址 |
| 1 | `MENU` | 18/23 | `格式 → 段落`、`格式 → 文字 → 小写`、`表格 → 转换 → 文本转换为表格` | ✅ **已实测全链路走通**（见 §5.1） |
| 2 | `TEXTSEL` | 14/23 | 选中前两段、选中最后一段、选中 "H2O" 里那一个字符 | ⚠️ 见 §5.6 |
| 3 | `KEY` | 13/23 | `Ctrl+A`、`Ctrl+E`、`Ctrl+H`、`Ctrl+F12`、`Ctrl+Enter`、`Ctrl+Shift+B` | ✅ 最省事的一类；Writer 有大量任务能靠快捷键绕开对话框 |
| 4 | `DIALOG` | 12/23 | 段落对话框（行距下拉 / 制表符标签页）、插入表格（行列数值框） | ⚠️ 数值框 ✅ / 下拉 ❌（见 §5.2） |
| 5 | `SELFMT` | 11/23 | 选中 → 套字体 / 字号 / 下划线 / 删除线 / 大小写转换 | ⚠️ 依赖 `TEXTSEL` |
| 6 | `TOOLBAR` | 8/23 | 字体名组合框、字号、居中、行距下拉、突出显示颜色 | ⚠️ 组合框输入可行，下拉同 §5.2 |
| 7 | `FILEDLG` | 2/23 | 导出 PDF；插入桌面上的 `1.png` | ⚠️ 同 §5.2 |
| 7 | `COLORPICK` | 2/23 | 清除黄色突出显示（选"无填充"）；按首字母给词染红/蓝 | ❌ 见 §5.2 |
| 9 | `APPOPT` | 1/23 | `工具 → 选项 → Writer → 基本字体` 改默认字体 | ✅ 可寻址；evaluator 读 `registrymodifications.xcu` |
| 9 | `INFEASIBLE` | 1/23 | 实时协作共享文档 | — |

**举例追溯**

- `MENU`：`0810415c`（前两段改双倍行距 —— 就是本项目 #1 已实测打通的那条链路）、
  `936321ce`（`表格 → 转换 → 文本转换为表格`）、`d53ff5ee` / `e528b65e`（`格式 → 文字 → 小写 / 词首字母大写`）
- `TEXTSEL`：`0b17a146`（把 "H2O" 中的 `2` 变下标 —— **单字符选择**）、
  `72b810ef`（最后一段加删除线）、`b21acd93`（三段分别单倍/双倍/1.5 倍行距）
- `DIALOG`：`0a0faba3`（段落对话框的"制表符"标签页：位置数值 + 右对齐单选 + 新建按钮）、
  `66399b0d`（插入表格 7 列 × 5 行，`config` 预先用 40 次方向键把光标放好）
- `COLORPICK`：`6a33f9b9`（突出显示改"无填充"）、`8472fece`（元音开头的词标红、其余标蓝 ——
  逐词染色，规模上更适合 Find & Replace 或宏）

---

## 5. a11y 层面的判断依据

以下每条都注明是**实测**（本仓库或 OSWorld 源码里的确凿证据）还是**推断**。

### 5.1 菜单 → 对话框：这条主干是通的（实测）

`docs/exec-plans/active/20260730-linux-a11y-first-osworld.md` 待办 #1 已在 Writer 上用纯
`element_index` 语义调用走通 `格式 → 段落 → 行距 → 双倍`，全程零坐标，并用
`Atspi.Text.get_default_attributes()` 读到 `line-height` 由 `100%` 变 `200%` 做真值确认。

这对本清单是最重要的好消息：三个应用里排名第一或第三的 `MENU`（Calc 30、Impress 25、Writer 18）
落在已验证可用的能力上。

但同一轮实测记录了三个必须注意的坑：

- **命名歧义**：子串匹配 `Format` 会同时命中 `menu Format`、`check menu item Formatting Marks`、
  `menu Formatting Mark`、`menu item Clone Formatting`。定位必须"角色 + 精确名"。
- **展开状态不可见**：点开菜单后 `EXPANDED` 状态为空，只能靠重读树发现多了菜单项。
- **菜单默认折叠**：Writer 里实测折叠了 102 处、共 726 个菜单项，每次进多级菜单要多一轮交互。
  本清单里 Calc 的 `插入 → 图表`、Impress 的 `幻灯片 → 幻灯片属性`、Writer 的
  `表格 → 转换 → 文本转换为表格` 都是二到三级，都要付这个成本。

### 5.2 下拉选择：已知断点，且覆盖面极广（实测）

待办 #1b 的结论：**LibreOffice 下拉里的选项必须坐标点击，`do_action` 不行。**

两个具体机制：

1. 树里那个 `combo box` 节点是**幻影** —— extents 是 `-2147483648,-2147483648 1x1`（INT_MIN 哨兵），
   根本没渲染。屏幕上真正的控件是它旁边的 `toggle button`。
2. 点开 toggle 后下拉作为**独立顶层 `window`** 弹出，内部是带 `Selection` 接口的 `table`，
   选项渲染为 `table cell`，agent 完全看得见 —— 但**没有任何语义路径能把选中提交下去**。
   已排除：`do_action` 到 cell、`Atspi.Selection.select_child`、裸 xdotool 方向键、
   MCP `press_key`、写 combo 的 `text` 兄弟节点。根因是 AT-SPI 说弹窗 `ACTIVE`，
   而 `xdotool getwindowfocus` 显示 X 输入焦点仍在主窗口，两个信号打架。

**这条断点在本清单里的暴露面**（按"下拉/调色板/过滤器选择"归并）：

| 应用 | 受影响任务数 | 来源类别 |
|---|---:|---|
| Calc | ≥ 14 | `COLORPICK` 3 + `FILEDLG` 2 + `DIALOG` 里带下拉的（单元格格式的类别列表、有效性的"允许"下拉、排序键下拉…） |
| Impress | ≥ 19 | `COLORPICK` 14 + `FILEDLG` 5 |
| Writer | ≥ 4 | `COLORPICK` 2 + `FILEDLG` 2，另加行距下拉等 |

调色板还多一层麻烦：它是 `ValueSet` 控件而不是按钮列表，且 Impress `986fc832` 明确要求
**按名字取"Dark Red 2"这个色块**。可行的规避路径是走"自定义颜色"对话框直接填 hex，
这也正好覆盖 Calc 那三个要求精确 `#00ff00` / `#ff0000` / `#0000ff` 的任务。

**结论**：这是三个应用共同的头号阻塞项，优先级应高于任何单个应用的打通工作。

### 5.3 单元格与幻灯片：只有视口内的元素在树里（实测，来自 OSWorld 源码）

`OSWorld/desktop_env/server/main.py:538-566` 对 Calc 的 `table` 节点做了特殊处理：
遍历时只收 `STATE_SHOWING` 为真的子节点，并在连续不可见时提前 break。原因是 Calc 的表格声明了
1024（或 16384）× 1048576 个单元格，全量遍历不可能。

也就是说 **`B1:E30` 这种范围里，滚出视口的行在 a11y 树里根本不存在**。任何"先在树里找到目标单元格
再操作"的策略在超出一屏时会直接失效。可行路径是名称框（`Name Box` 组合框）输入区域跳转，
或方向键 / `Ctrl+Home` 导航，本质是"先让它可见，再读树"。

同一约束适用于 Impress 的幻灯片面板：`3161d64e` 的"第 14 页"、`edb61b14` 的"最后一页"
都需要先滚动。

本仓库的 `render_visible_cells()` 已经为可见单元格补上了 Frame 和真实角色（`table cell` 而非 `cell`），
这是坐标寻址在下拉/表格上能工作的前提。

### 5.4 数据透视表布局：拖拽是硬缺口（推断）

Calc 有 5 个任务需要建数据透视表（`1954cced`、`1de60575`、`51719eea`、`535364ea`、`30e3e107`），
evaluator 检查 `col_fields` / `row_fields` / `data_fields` / `filter` 四项，也就是**字段落在哪个区域会被判分**。

LibreOffice 的透视表布局对话框，标准操作是把字段按钮从"可用字段"拖到"行字段/列字段/数据字段"框里。
AT-SPI 没有对应的语义动作，本项目的 `drag` 也只能合成坐标拖拽。
虽然该对话框有 `>>` 之类的移动按钮可作为替代，但**尚未实测**。

图表向导（另外 5 个任务）情况好一些：它是常规的多页对话框（图表类型列表 + 数据区域文本框 + 标题文本框），
但类型选择又落回 §5.2 的列表选中问题。

### 5.5 Impress 画布对象：a11y 层最薄弱的一环（推断）

`CANVASOBJ` 在 Impress 排第 2（29/47），是这个应用最核心的操作，也是最没有把握的一环：

- instruction 大量使用**空间与序号指代**："三个文本框自上而下"（`04578141`）、
  "第一个文本框"（`05dd4c1d`、`3161d64e`）、"第 3 页上的表格"（`ac1b39ff`）。
  即便树里能列出这些形状，**"自上而下第几个"这个信息只能从 extents 的坐标推出来**，
  不是名字能给的。
- 7 个 `DRAGGEOM` 任务要求把对象挪到"底部/右侧/顶部"，AT-SPI 完全没有移动语义。
  F4 位置和大小对话框是唯一的非坐标出路，而且它把"挪到底部"这种相对描述变成了
  "得先知道页面高度和对象高度再算 Y 值"的算术题。
- 5 个 `VISUAL` 任务（`0a211154`、`a53f80cd`、`70bca0cc`、`986fc832`、`b8adbc24`）
  的判断依据根本不在 a11y 树里。

**给 #2b 的建议**：Impress 应该排在 Writer/Calc 之后。它同时踩 §5.2（14 个颜色任务）、
§5.5（画布）和 VLM 依赖三个坑，不适合当第一个打通目标。

### 5.6 精确文本选择：机制存在，边界未测（推断 + 部分实测）

AT-SPI 的 `Text` 接口提供 `set_caret_offset` / 选区设置，Writer 的正文是
`document-frame` + `Text`，理论上可以按偏移量选中任意范围。本仓库的 `type_text` 已改为
caret 插入 + 选区替换并有回读确认，说明这条通路是活的。

但本清单里的需求更细：`0b17a146` 要选中 "H2O" 里那**一个字符**，`0810415c` 要选中
**前两个段落**，`72b810ef` 要选中**最后一段**。这些都需要"文本内容 → 偏移量"的映射，
目前没有实测证据说明可靠。

一个已确认可用的真值来源：`Atspi.Text.get_default_attributes()` 能读到 `line-height`
等段落级属性，可以用来验证格式类任务是否真的生效（注意 `Atspi.Text.get_attributes()`
在实测的这个版本不存在，`Accessible.get_attributes()` 只给 `level` / `heading-level`）。

### 5.7 `SAVE`：覆盖 107/117 个任务的隐形失败点（实测，来自任务文件）

107 个任务的 `evaluator.postconfig` 是同一个模式：

```json
[{"type": "activate_window", "parameters": {"window_name": "...xlsx - LibreOffice Calc", "strict": true}},
 {"type": "sleep", ...},
 {"type": "execute", "parameters": {"command": ["python", "-c", "import pyautogui; pyautogui.hotkey(\"ctrl\", \"s\")"]}}]
```

三点影响：

1. **窗口标题必须仍然匹配**（`strict: true`）。改了文件名或关了文档，判分直接拿不到窗口。
2. **xlsx / docx / pptx 存盘会弹"保持当前格式"警告框**。这是个标准模态对话框，
   语义可寻址，但如果 agent 存完盘不处理它，警告框会一直挂着 —— 而且它是模态的，
   会挡住之后所有操作。本仓库已修过"模态对话框对 agent 不可见"的缺陷，这条链路值得单独回归。
3. 判分脚本自己会再按一次 `Ctrl+S`。这意味着**如果 agent 在 GUI 里动过文档但没存，
   判分时内存里的版本会覆盖磁盘**；反过来，如果 agent 绕过 GUI 直接改磁盘文件而
   LibreOffice 里那份是"未修改"状态，这次 `Ctrl+S` 是空操作、改动能留住。

### 5.8 稳定性风险（实测）

待办 #1b 记录：**LibreOffice 在密集自动化下反复退出**，导致一次连贯的 MCP 端到端跑未能完成。
本清单里的任务普遍是多步链路（Calc 的图表向导 4 步、Impress 的多幻灯片遍历），
打通工作需要先解决进程存活问题，否则会把稳定性问题误读成功能问题。

---

## 6. 哪些任务本质上必须走 GUI

按"绕不开的程度"分四档。

### A 档：判分读的是应用配置，不是文档（3 个）

| 任务 | evaluator | 说明 |
|---|---|---|
| impress `0f84bef9` | `check_presenter_console_disable` | 读 `~/.config/libreoffice/4/user/registrymodifications.xcu` |
| impress `2cd43775` | `check_auto_saving_time` | 同上，`config` 只 `launch` 一个空 Impress |
| writer `f178a4a9` | `find_default_font` | 同上 |

文档内容完全无关，唯一的正规路径是 `工具 → 选项` 对话框。

### B 档：判分读的是运行中应用的 a11y 树（1 个）

| 任务 | evaluator | 说明 |
|---|---|---|
| impress `ef9d12bd` | `check_left_panel`，`result.type = "accessibility_tree"` | 判据是树里存在 `document-frame[@name="Slides View"]` |

`config` 会先用 `pyautogui` 走 `F10 → 右 2 → 下 11 → Enter` 把左侧面板关掉。
**这个任务的答案就是 UI 状态本身**，无论如何绕不开 GUI。

### C 档：`postconfig` 把文档窗口钉死（107 个）

见 §5.7。严格说这不是"必须 GUI"，而是"必须让 LibreOffice 里那份文档处于正确状态"——
GUI 操作和 UNO/宏驱动都满足，纯离线改文件则有被覆盖的风险（取决于文档是否被标脏）。

### D 档：功能本身没有干净的文件级等价物

这些任务即使允许离线改文件，也要么写起来极其别扭，要么就是在复刻 LibreOffice 的行为：

- Calc：`1334ca3e`（缩放级别）、`4188d3a4`（冻结）、`6054afcb`（隐藏行）、
  `ecb0df7a`（数据有效性）、`0cecd4f3`（表复制与顺序）、`a01fbce3`（区域设置）、
  5 个透视表 + 5 个图表任务
- Impress：`21760ecb`（幻灯片切换效果）、`ce88f674`（纸张方向）、`ac9bb6cb`（母版里的页码）、
  全部 7 个 `DRAGGEOM` 与 5 个 `VISUAL`
- Writer：`0e47de2a`（页脚页码）、`0a0faba3`（制表位）、`adf5e2c3`（交叉引用）、`ecc2413d`（分页符）

### 反向标注：最容易被非 GUI 路径"绕过"的任务

近 **30 个 Calc 任务的 evaluator 只查 `sheet_data` / `sheet_print` / `style` / `check_cell`**
（`01b269ae`、`4e6fcf72`、`7e429b8d`、`d681960f`、`a9f325aa`、`abed40dc` 等），
也就是纯数值、公式或单元格格式，openpyxl 几行就能写完；**6 个 Writer 任务是纯文本变换**
（`d53ff5ee`、`e528b65e`、`6f81754e`、`88fe4b2d`、`8472fece`、`e246f6d8`），python-docx 同理；
**4 个导出任务**（calc `3aaa4e37` / `aa3a8974`、writer `4bcb1253`、impress `455d3c66`）
`soffice --convert-to` 一条命令就够。

这批任务对"验证 a11y 能力"没有价值 —— 它们量的是 agent 会不会用 shell，不是 GUI 链路是否可用。
**#2b 选任务级验收目标时应避开它们**，优先选 A / B / D 档。

---

## 7. 给 #2b 的推荐打通顺序

1. **先修 §5.2 的下拉提交**。它横跨三个应用、影响至少 37 个任务，是唯一一个"修好了三边都受益"的点。
   当前唯一可用路径是坐标点击（需 `OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS=1`），
   属于"能跑通但破坏 a11y-first 论证"，值得再找一次纯语义路径。
2. **Writer 先于 Calc 先于 Impress**。Writer 的高频类别（`MENU` 18 / `KEY` 13）落在已验证能力上，
   `TEXTSEL` 是唯一新增未知项；Calc 多一个视口约束（§5.3）和透视表拖拽（§5.4）；
   Impress 的画布问题（§5.5）最深。
3. **任务级验收目标建议**（都避开了"可用脚本绕过"的那批）：
   - Writer：`0810415c`（已有链路，可直接当回归）、`936321ce`（`表格 → 转换` 三级菜单 + 单选对话框）
   - Calc：`4188d3a4`（`视图 → 冻结`，纯菜单无对话框，最干净）、
     `ecb0df7a`（`数据 → 有效性`，正好压中下拉提交这个断点）
   - Impress：`ce88f674`（纸张方向单选，画布无关）、`21760ecb`（侧边栏面板选中）
4. **`SAVE` 单独做一次回归**：xlsx / docx / pptx 三种格式的"保持当前格式"警告框各测一次。
   它覆盖 107 个任务，漏掉它等于所有任务都判 0 分。

---

## 附录：全量任务 → 操作标签

标签含义见 §1。同一任务多标签，顺序无意义。

### libreoffice_calc

| id | 标签 |
|---|---|
| `01b269ae` | RANGE CELL MENU KEY SAVE |
| `0326d92d` | CELL RANGE MENU WIZARD DIALOG SAVE |
| `035f41ba` | CELL SHEETTAB MENU DIALOG SAVE |
| `04d9aeaf` | CELL SHEETTAB MENU DIALOG SAVE |
| `0a2e43bf` | CELL RANGE MENU WIZARD DIALOG SAVE |
| `0bf05a7d` | CELL RANGE MENU DIALOG KEY SAVE |
| `0cecd4f3` | SHEETTAB CONTEXT DIALOG SAVE |
| `12382c62` | RANGE MENU WIZARD DIALOG SHEETTAB SAVE |
| `1273e544` | RANGE KEY SHEETTAB MENU DIALOG CELL SAVE |
| `1334ca3e` | MENU DIALOG SAVE |
| `1954cced` | RANGE MENU WIZARD DIALOG SAVE |
| `1d17d234` | SHEETTAB MENU DIALOG RANGE CELL TOOLBAR SAVE |
| `1de60575` | RANGE MENU WIZARD DIALOG SHEETTAB SAVE |
| `1e8df695` | CONTEXT RANGE CELL SAVE |
| `21ab7b40` | CELL RANGE MENU DIALOG COLORPICK SELFMT SAVE |
| `21df9241` | RANGE MENU DIALOG KEY SAVE |
| `26a8440e` | SHEETTAB MENU DIALOG CELL SAVE |
| `2bd59342` | INFEASIBLE |
| `30e3e107` | SHEETTAB MENU DIALOG RANGE CELL COLORPICK TOOLBAR SELFMT WIZARD SAVE |
| `347ef137` | RANGE MENU WIZARD DIALOG SAVE |
| `357ef137` | CELL SAVE |
| `37608790` | CELL RANGE MENU DIALOG SAVE |
| `3a7c8185` | RANGE MENU DIALOG TOOLBAR WIZARD SAVE |
| `3aaa4e37` | MENU FILEDLG DIALOG |
| `4172ea6e` | CELL SAVE |
| `4188d3a4` | CELL MENU SAVE |
| `42e0a640` | SHEETTAB MENU DIALOG CELL SAVE |
| `4de54231` | CELL RANGE KEY SAVE |
| `4e6fcf72` | CELL SAVE |
| `4f07fbe9` | CELL SAVE |
| `51719eea` | CELL SHEETTAB RANGE MENU WIZARD DIALOG SAVE |
| `51b11269` | RANGE MENU DIALOG TOOLBAR SAVE |
| `535364ea` | RANGE MENU WIZARD DIALOG SHEETTAB SAVE |
| `6054afcb` | RANGE CONTEXT MENU SAVE |
| `6e99a1ad` | RANGE MENU DIALOG KEY TOOLBAR SAVE |
| `7a4e4bc8` | RANGE CONTEXT KEY SAVE |
| `7e429b8d` | CELL SAVE |
| `7efeb4b1` | CELL RANGE SAVE |
| `8b1ce5f2` | RANGE COLORPICK DIALOG TOOLBAR SELFMT SAVE |
| `a01fbce3` | RANGE MENU DIALOG APPOPT SAVE |
| `a9f325aa` | CELL SAVE |
| `aa3a8974` | MENU DIALOG FILEDLG |
| `abed40dc` | CELL SAVE |
| `d681960f` | CELL SAVE |
| `eb03d19a` | RANGE KEY DIALOG CELL SAVE |
| `ecb0df7a` | RANGE MENU DIALOG SAVE |
| `f9584479` | CELL RANGE TOOLBAR SAVE |

### libreoffice_impress

| id | 标签 |
|---|---|
| `04578141` | SLIDENAV CANVASOBJ TEXTSEL COLORPICK SELFMT SAVE |
| `05dd4c1d` | SLIDENAV CANVASOBJ TEXTSEL TOOLBAR KEY SELFMT SAVE |
| `08aced46` | SLIDENAV CANVASOBJ TEXTSEL TOOLBAR SELFMT SAVE |
| `0a211154` | SLIDENAV MENU DIALOG COLORPICK CANVASOBJ TEXTSEL VISUAL SAVE |
| `0f84bef9` | APPOPT MENU DIALOG |
| `15aece23` | SLIDENAV CANVASOBJ DRAGGEOM DIALOG KEY SAVE |
| `21760ecb` | SLIDENAV SIDEBAR MENU SAVE |
| `2b94c692` | SLIDENAV CANVASOBJ DRAGGEOM SAVE |
| `2cd43775` | APPOPT MENU DIALOG |
| `3161d64e` | SLIDENAV CANVASOBJ TEXTSEL TOOLBAR SELFMT SAVE |
| `358aa0a7` | VIEWMODE KEY TOOLBAR SELFMT SAVE |
| `39be0d19` | SLIDENAV MENU DIALOG SAVE |
| `3b27600c` | MENU DIALOG COLORPICK SAVE |
| `455d3c66` | MENU FILEDLG DIALOG SAVE |
| `4ed5abd0` | SLIDENAV CANVASOBJ TEXTSEL COLORPICK TOOLBAR KEY SELFMT SAVE |
| `550ce7e7` | SLIDENAV CANVASOBJ TEXTSEL MENU SELFMT SAVE |
| `57667013` | SLIDENAV CANVASOBJ TEXTSEL COLORPICK SELFMT SAVE |
| `5c1a6c3d` | SLIDENAV CANVASOBJ TEXTSEL KEY TOOLBAR SELFMT SAVE |
| `5cfb9197` | SLIDENAV CANVASOBJ TEXTSEL SAVE |
| `5d901039` | SLIDENAV CANVASOBJ DIALOG KEY MENU DRAGGEOM SAVE |
| `70bca0cc` | SLIDENAV CANVASOBJ COLORPICK DIALOG MENU VISUAL SAVE |
| `73c99fb9` | SLIDENAV CANVASOBJ TEXTSEL SAVE |
| `7ae48c60` | SLIDENAV CANVASOBJ DIALOG KEY SAVE |
| `7dbc52a6` | SLIDENAV VIEWMODE TEXTSEL CANVASOBJ KEY SELFMT MENU SAVE |
| `841b50aa` | VIEWMODE TEXTSEL MENU DIALOG COLORPICK SAVE |
| `8979838c` | VIEWMODE TEXTSEL MENU DIALOG COLORPICK SAVE |
| `986fc832` | CANVASOBJ TEXTSEL COLORPICK TOOLBAR KEY SELFMT VISUAL SAVE |
| `9cf05d24` | SLIDENAV MENU DIALOG COLORPICK SAVE |
| `9ec204e4` | SLIDENAV CONTEXT DRAGGEOM SAVE |
| `a097acff` | MENU FILEDLG DIALOG KEY |
| `a434992a` | CANVASOBJ TEXTSEL TOOLBAR COLORPICK MENU DIALOG SELFMT SAVE |
| `a53f80cd` | SLIDENAV CANVASOBJ TEXTSEL COLORPICK KEY SELFMT VISUAL SAVE |
| `a669ef01` | SLIDENAV TEXTSEL MENU DIALOG KEY SAVE |
| `ac1b39ff` | SLIDENAV CANVASOBJ DRAGGEOM DIALOG SAVE |
| `ac9bb6cb` | VIEWMODE CANVASOBJ COLORPICK MENU SELFMT SAVE |
| `af23762e` | MENU VIEWMODE SAVE |
| `af2d657a` | SLIDENAV CANVASOBJ TEXTSEL TOOLBAR SELFMT SAVE |
| `b8adbc24` | SLIDENAV CANVASOBJ TEXTSEL DIALOG COLORPICK TOOLBAR SELFMT VISUAL SAVE |
| `bf4e9888` | SLIDENAV CONTEXT SIDEBAR MENU FILEDLG DRAGGEOM DIALOG SAVE |
| `c59742c0` | MENU FILEDLG SAVE |
| `c82632a4` | SLIDENAV MENU FILEDLG DIALOG KEY CANVASOBJ SAVE |
| `ce88f674` | MENU DIALOG SAVE |
| `e4ef0baf` | SLIDENAV CANVASOBJ DIALOG TEXTSEL TOOLBAR KEY SELFMT SAVE |
| `ed43c15f` | SLIDENAV CANVASOBJ DRAGGEOM DIALOG TEXTSEL KEY SELFMT SAVE |
| `edb61b14` | SLIDENAV CANVASOBJ TEXTSEL TOOLBAR SELFMT SAVE |
| `ef9d12bd` | MENU VIEWMODE |
| `f23acfd2` | CANVASOBJ TEXTSEL TOOLBAR MENU SAVE |

### libreoffice_writer

| id | 标签 |
|---|---|
| `0810415c` | TEXTSEL MENU DIALOG TOOLBAR SELFMT SAVE |
| `0a0faba3` | TEXTSEL MENU DIALOG KEY SAVE |
| `0b17a146` | TEXTSEL KEY MENU DIALOG SELFMT SAVE |
| `0e47de2a` | MENU TOOLBAR KEY TEXTSEL SAVE |
| `0e763496` | KEY TOOLBAR SELFMT SAVE |
| `3ef2b351` | TEXTSEL KEY TOOLBAR SELFMT SAVE |
| `4bcb1253` | MENU DIALOG FILEDLG |
| `66399b0d` | MENU DIALOG KEY SAVE |
| `6a33f9b9` | KEY COLORPICK TOOLBAR SELFMT SAVE |
| `6ada715d` | MENU FILEDLG SAVE |
| `6f81754e` | MENU DIALOG KEY TEXTSEL SAVE |
| `72b810ef` | TEXTSEL MENU SELFMT SAVE |
| `8472fece` | TEXTSEL COLORPICK TOOLBAR SELFMT SAVE |
| `88fe4b2d` | TEXTSEL KEY MENU DIALOG SAVE |
| `936321ce` | TEXTSEL MENU DIALOG SAVE |
| `adf5e2c3` | TEXTSEL MENU DIALOG SAVE |
| `b21acd93` | TEXTSEL MENU DIALOG TOOLBAR SELFMT SAVE |
| `bb8ccc78` | INFEASIBLE |
| `d53ff5ee` | KEY MENU SELFMT SAVE |
| `e246f6d8` | MENU DIALOG KEY TEXTSEL TOOLBAR SELFMT SAVE |
| `e528b65e` | KEY MENU SELFMT SAVE |
| `ecc2413d` | TEXTSEL KEY MENU SAVE |
| `f178a4a9` | APPOPT MENU DIALOG |
