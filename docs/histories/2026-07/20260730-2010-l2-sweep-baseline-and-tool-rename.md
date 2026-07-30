## [2026-07-30 20:10] | Task: L2 七应用扫尾 + 四元组基线 + 工具改名（#3 / #26 / #27 / #29成本侧 / #30）

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME + at-spi2-core 2.44）`
* **覆盖范围**: `71c3cfa..HEAD` 共 41 次提交

### 📥 User Query
> 我们现在要做的就是让执行变得可靠 / 至少这个 mcp/tool 不能骗 agents
>
> （随后）#27 选 C：直接改名；Thunderbird：装 dovecot 解锁它

### 🛠 Changes Overview

**待办完成**：`#3`（效果判据）、`#26`（四元组基线）、`#27`（工具改名）、
`#30`（崩溃恢复）；`#29` 完成成本侧。

**代码**（`apps/OpenComputerUseLinux/`）：
- `invoke_element_action`：`perform_secondary_action` 改名（#27 方案 C）
- 纯合成工具区分"送达"与"生效"（`deliveryWasVerified`）
- 下拉项跳过语义通道（`is_dropdown_item`）
- Electron/Gecko 动作名：认 `doDefault` / `check` / `uncheck`，排除
  `clickAncestor` 与 `click ancestor`（归一化比对）
- Qt 富文本 tooltip 剥离、Calc 空单元格跳过、动作表一次读完
- 夺焦点失败时报出焦点持有者

**脚本**：`verify-libreoffice-crash-recovery.py`、`measure-baseline.py`

### 🧠 Design Intent (Why)

#### 一、执行失败有五类，处置各不相同

| 类型 | 实例 | 处置 |
|---|---|---|
| 什么都没发生 | GIMP 图层 `activate` | `auto` 自动回落坐标（外部信号判定） |
| 做了别的事 | LibreOffice 下拉提交 | **调用前**就避开语义通道 |
| 状态变了行为没变 | VLC Simple/All 单选 | **接不住**，需任务级语义；可用元素锚定 `global` |
| 点击导致焦点丢失 | Monaco 编辑器 | 别点，全程键盘；丢了用 `ctrl+1` |
| 焦点被树外的东西拿走 | VS Code 原生对话框 | 报出诊断 + 引导截图 |

第三类的边界写进了 `actionResult` 的注释——**通用判据接不住"状态变了行为没变"，
因为树确实变了**。不假装机制万能，比多接住一类更重要。

#### 二、三例「a11y 结构性看不见」，与环境阻塞必须分开

GIMP 画布（整棵树里 `canvas` 角色节点数为 **0**）、VS Code 的原生文件对话框、
VS Code 的重启提示。这三例**换什么环境都测不了**；而 Thunderbird 缺账户
**换个环境就能测**。两者混在一起统计，会同时高估环境问题、低估 a11y 的结构盲区——
后者恰恰是 #29 收益侧要单独计数的东西。

#### 三、基线必须盖住最差的工具包

加入 Electron 后 a11y 占比从 69% 掉到 **52%**、平均 token 从 7026 涨到 **10157**。
Electron 那条 4 个动作全是键盘合成（0% 语义）、22679 token，两项都垫底——
**正因如此它必须在基线里**。把最不可靠的排除在外，指标会显著好看于实际，
而这个基线将来是 #29 A/B 的基准，偏了就全错。

#### 四、两个被实测推翻的既有结论

- **a11y 树并非普遍更便宜**：gedit 树 349 token vs 截图 1014（截图贵 2.9x），
  但文件管理器树 2135 vs 截图 756（**截图便宜 0.4x**）。
  所以 a11y 优先真正立得住的理由是**可操作性**（树给出能下手的 `element_index`），
  不是省 token。`serverInstructions` 里原本暗示成本优势的措辞已改掉并加断言。
- **Monaco 并非只能纯键盘**：整份文档内容完整暴露在 `entry` 的 Value 里，
  **读是语义可行的**，只有写要回落键盘。

### ✅ Verification

七个应用六个闭合，每一项都以**外部真值**验收：

| 应用 | 工具包 | 判据 |
|---|---|---|
| Nautilus | GTK | 文件系统 + wmctrl 窗口标题 |
| Writer | VCL | `content.xml` → `line-height="200%"` |
| Calc | VCL | CSV → `Apple,3` 变 `Fig,3` |
| VS Code | Electron | 磁盘文件 → `MULTIPLY = 42` |
| VLC | Qt | `vlcrc` → `qt-pause-minimized=1` |
| GIMP | GAIL | 画布采样 21875 像素，21871 变化 |

崩溃恢复端到端通过；基线 5/5 通过；CI 全绿。

### 📌 Notes

**这一轮我至少七次差点固化未经验证的说法**，六次自己抓回来、一次被推着做才发现：

| 差点写死的 | 实际 |
|---|---|
| `[disabled]` 标记 | Nautilus 文件图标不设 ENABLED 却完全可操作 |
| `[clickable]` 命名 | 它保证"有动作"，不是"点得动"→ 改 `[has-click-action]` |
| instructions `will work` / `cheap` | 前者会撒谎，后者在文件管理器上是反的 |
| LibreOffice `Yes` 撒谎 | 是我叠了 5 个弹窗造成的 |
| 名称框跳转 | 未验证就写进了给 agent 的提示 |
| **Thunderbird 需要真实账户** | **局部验证当成全称结论，还贴了"已实测"标签** |

最后一条最值得记：我用**消息过滤器**打不开这一个证据，推广成了全部 10 个任务的结论。
后来本地账户就打通了其中 5 个。**局部验证不能当全称结论用**——这和工具
"不许替 agent 打包票"是同一条纪律，只是对象换成了我自己。

**另有五条被证伪的路径**（手工造 mbox 的五种做法）记在 plan 里。
一份"哪些路已堵死"的清单，价值不低于"哪些路走通了"。
