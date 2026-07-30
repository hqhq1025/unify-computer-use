# Linux a11y-first Computer Use：面向 OSWorld 的可靠性与效率攻坚

状态：active ｜ 创建于 2026-07-30

## 目标

让 Claude Code / Codex 通过这个 MCP，以 **a11y 为主、VLM 为辅** 的方式操作 Linux 桌面，
并在 OSWorld 上取得比 VLM-first 更好的**成功率与单位成本**。

判断这条路走通的标准不是"能不能拿到 accessibility tree"——实测已证明绝大多数应用都能拿到
（见下方基线）——而是三件事：

1. **动作可信**：每个动作要么可验证地生效，要么明确报错。不存在"报成功但什么都没发生"。
2. **观测便宜**：单次观测的 token 量控制在可接受范围，否则 a11y 相对截图的成本优势不成立。
3. **呈现有效**：给 agent 的树是它能用的形态，而不是 AT-SPI 的原始转储。

第 3 点原本被当成纯研究议题，但对照 macOS 参考实现后发现：**其中相当一部分不是研究，
是没跟上的移植**（见"对齐 macOS 参考实现"）。真正需要实验的只剩 macOS 没有回答的问题。

## 范围

- 包含：
  - `apps/OpenComputerUseLinux` 的可靠性缺陷修复
  - accessibility tree 的裁剪（pruning）与呈现形态（rendering）研究与实现
  - 应用 a11y 就绪度探测工具，以及 OSWorld 环境所需的解锁配置
  - harness 侧的观测策略：a11y 优先、VLM 按需触发
- 不包含：
  - 浏览器内的操作。浏览器走 browser-use + Playwright 独立控制平面（见"跨平面边界"）
  - macOS / Windows runtime 的对齐改造
  - 刷 OSWorld 排行榜。本计划不追求与官方 leaderboard 的可比性，理由见"决策记录"

## 背景

- 相关文档：
  - `docs/ARCHITECTURE.md` 第 7 节（Linux Runtime）
  - `docs/histories/2026-07/20260729-2328-fix-linux-silent-input-failures.md`
  - `docs/histories/2026-07/20260729-1520-fix-linux-large-tree-traversal.md`
  - `docs/histories/2026-07/20260729-1535-fix-linux-click-hierarchy.md`
- 相关代码路径：
  - `apps/OpenComputerUseLinux/runtime.py`（AT-SPI bridge：遍历、渲染、动作）
  - `apps/OpenComputerUseLinux/main.go`（MCP 协议面、tool schema、快照缓存）
  - `scripts/verify-linux-input-chain.py`（真实桌面端到端验证）
- 已知约束：
  - AT-SPI 的输入合成是全局的（XTEST），无法像 macOS `CGEvent.postToPid` 那样进程定向投递
  - AT-SPI 多个写入 API（`insert_text` / `set_text_contents`）返回值不可信，必须回读确认
  - snap 封装的应用接不上会话 a11y 总线，属于打包层问题，运行时无法绕过

## 基线（2026-07-30 实测，Ubuntu 22.04 + X11 GNOME + at-spi2-core 2.44）

单次 `get_app_state` 的规模与成本。token 按 chars/4 粗估：

| 应用 | 原始节点 | 屏幕可见 | 可见占比 | MCP 输出 | ≈token |
|---|---:|---:|---:|---:|---:|
| LibreOffice Writer | 1933 | 184 | 9.5% | 43 KB | 10.8k |
| GIMP | 1720 | 203 | 11.8% | 44 KB | 11.0k |
| VS Code（+解锁） | 602 | 556 | 92% | 85 KB | 21.3k |
| Chrome（+解锁） | 270 | 120 | 44% | 23 KB | 5.8k |
| Nautilus | 239 | 78 | 33% | 12 KB | 3.0k |
| gedit | 242 | 31 | 12.8% | 7.7 KB | 1.9k |
| VLC | 191 | 129 | 68% | 3.7 KB | 0.9k |
| Thunderbird | 177 | 116 | 66% | 13 KB | 3.2k |
| gnome-terminal | 90 | 20 | 22% | 2.8 KB | 0.7k |
| Firefox（snap） | — | — | — | — | 接不上 AT-SPI |

耗时基线（gedit，438 节点）：树遍历 363 ms、截图 30 ms、完整快照 476 ms。
**截图不是耗时瓶颈，树遍历才是；而 token 成本比耗时更值得优化。**

**观测里的截图占比（gedit 实测）**：文本 1908 token + 截图 1014 token（952x799 PNG，65 KB）。
截图目前占单次观测的 **35%**，且 `build_snapshot()` 是**无条件**调用 `capture_window_png()` 的——
`get_app_state` 和所有动作工具的返回都带图。也就是说当前实现每次调用都在同时付 a11y 和 VLM 的钱。
一旦裁剪把文本砍掉 ~88%，截图占比会升到 **80%** 左右，成为新的主要成本。
因此**双轨拆分的优先级高于裁剪**。

关键观察：GTK 系应用约 **88% 的节点不在屏幕上**，这是文本侧最大的单点优化空间。
`可交互` 计数存在虚高——LibreOffice 的 1402 个可交互节点里大部分是未展开的菜单项，
树里存在但当前点不到。

## 成功标准

**S1 动作可信度（硬门槛）**
- 静默失败率 = 0：动作要么可验证生效，要么返回 `isError`
- `get_app_state` 不把"应用活着但 a11y 是空壳"伪装成正常状态

**S2 观测成本**
- 默认观测**不含截图**；截图只在明确需要时出现
- 上表中位数应用的单次观测 ≤ **1.5k token**（当前中位约 3.2k 文本 + 1k 截图，最差 21.3k + 截图）
- 裁剪后**不丢失任务关键元素**（度量方法见清单 #7）

**S3 端到端能力**
- 先在 OSWorld 子集上量出 baseline，再据此设定成功率目标
- 报告口径固定为四元组：**成功率 / 平均步数 / 平均 token / a11y 通道使用率**。
  最后一项来自 OSWorld-MCP 的教训——它报告的最高 Tool Invocation Rate 只有 33.3%，
  工具做得好不代表 agent 会用；不度量这一项就无法区分"工具不行"和"没被调用"

## 推进顺序

**先把 Linux 链路的缺陷排查干净、测试做扎实，再接 OSWorld。**

理由：baseline-first 这条原则适用于**优化**（裁剪、呈现改造这类"改了不知道有没有变好"的工作），
不适用于**缺陷修复**——"报成功但什么都没发生"是自明的错误，不需要基线来证明。
反过来，在一个已知存在静默失败的 runtime 上跑 OSWorld，量到的是 bug 而不是能力，
那个基线本身是噪音，后续所有对比都建立在流沙上。

所以 OSWorld 接入排在可靠性工作之后（待办 #25、#26）；其余项都不依赖它。

## 工作面（不分阶段，全部并行推进；具体待办见文末清单）

### 可靠性
- ~~僵尸 AT-SPI 注册~~ **诊断错误，已撤销**。2026-07-30 严格复现：gedit / VLC 在
  SIGKILL 与 SIGTERM 下，AT-SPI 均在 2 秒内干净注销，不存在残留注册。
  原判断源于用 `grep -c "[g]oogle-chrome"` 数进程返回 0，而真实进程名是
  `/opt/google/chrome/chrome`——当时 Chrome 其实活着（11 个进程），
  那个"2 节点空壳"是**未开启 a11y 的活应用**，不是僵尸
- [x] **空壳 a11y 静默成功**（上一条的真实根因）：应用活着、窗口正常，但 a11y 树里
  只有一个窗口框时，`get_app_state` 返回 `isError=false` 且毫无提示，
  agent 无从分辨"界面是空的"与"我看不见这个界面"。已加诊断提示，
  同时作为切到 VLM 通道的信号
- `click` / `press_key` 的效果仍无法区分"生效"与"送达但无响应"，需补充判据
- 系统性排查：对基线表里的 9 个可用应用逐个走完整动作集
  （`click` / `type_text` / `press_key` / `scroll` / `drag` / `set_value` / `perform_secondary_action`），
  每个动作用 AT-SPI 真值做 before/after 判定，把失败面完整列出来再逐条修
- 每修一条，同步补回归测试（`runtime_test.py` 单测 + `verify-linux-input-chain.py` 端到端）

### 观测双轨拆分
- 把 a11y 与 VLM 拆成两条显式 track，**a11y 为默认，VLM 为兜底**
- **a11y track 不带截图**。只有 a11y 确实做不到某个功能时，才切到 VLM track
- `build_snapshot()` 不再无条件截图；动作工具的返回默认不带图（当前带）
- 需要确定的接口形态：是加参数、加独立 tool，还是由 server 依据信号自动决定
- 切轨信号可直接复用现有标注：`Delivery ... was not verified`、`Nothing observable changed`、
  以及 a11y 树为空/过小

### 对齐 macOS 参考实现
Linux runtime 是从 macOS 移植过来的，但大量逻辑没有跟上。与其自己重新摸索失败面，
不如**逐条对照参考实现**——macOS 侧已经在真实应用上验证过。

规模对比：macOS 核心逻辑约 4900 行 Swift（`AccessibilitySnapshot.swift` 1974 +
`ComputerUseService.swift` 1890 + `AppDiscovery.swift` 529 + `InputSimulation.swift` 369 +
`KeyMapping.swift` 165），Linux 侧 `runtime.py` 只有 1419 行。

已识别的能力缺口（2026-07-30 对照 `packages/OpenComputerUseKit/Sources/OpenComputerUseKit/`）：

| macOS 能力 | Linux 现状 | 对应本计划的哪一项 |
|---|---|---|
| `shouldSkipChild` | 缺失 | H3 结构容器过滤 |
| `isPlainGenericTextContainer` | 缺失 | H1/H3 树压缩 |
| `placeholderValue` | 缺失 | 避免把 placeholder 当成真实内容 |
| `isSiblingCounterText` | 缺失 | 噪音过滤 |
| `recoverVisibleWindow` | 缺失 | 窗口抬升，当前是自己实现的 `focus_window` |
| `enableBestEffortAccessibilityModes` | 缺失 | Chrome/VS Code 的 a11y 解锁 |
| `outlineRowSummary` / `flattenedRowTexts` | 缺失 | 表格/大纲呈现 |
| `markdownLinkText` | 缺失 | 链接渲染 |
| `isUsableWindowElement` | 部分 | 僵尸注册判定 |
| `preferredFocusedElement` | 部分 | 焦点元素解析（已修一部分） |
| `shouldSuppressChildren` | 部分 | 已有 `MANAGES_DESCENDANTS` 分支 |
| `meaningfulActions` | 已移植 | 对应 `CLICK_COVERED_ACTIONS` |

**移植路线已被验证可行**：`meaningfulActions` → `CLICK_COVERED_ACTIONS` 就是一次成功的对照移植
（见 `docs/histories/2026-07/20260729-1535-fix-linux-click-hierarchy.md`）。

注意：不是无脑照搬。AX 与 AT-SPI 的语义不同（例如 AT-SPI 没有进程定向输入投递），
每条移植都要判断哪些是平台无关的产品判断（可搬），哪些是 macOS 特有的机制（不可搬）。

### 引导 agent 走 a11y 通道
使用率不是只能被动度量的结果，**MCP 自身就是主要的引导手段**。
OSWorld-MCP 报告的最高 Tool Invocation Rate 只有 33.3%，说明"工具可用"离"工具被用"
差得很远，而这段距离主要由工具的自我描述和返回内容决定。

可用的引导杠杆（按影响力排序）：

1. **工具描述里写清优先级与代价差异**。已有先例：`click` 的描述现在写着
   "PREFER element_index：它调用元素自身的 accessibility action，可靠且**不抢焦点**；
   x/y 坐标点击会合成真实鼠标事件并**抢焦点**"。这类"两条路的真实差异"比单纯说
   "推荐用 A"有效得多
2. **`serverInstructions` 里给出选择规则**，而不只是罗列工具
3. **动作返回的 `Note:` 行做即时纠偏**。当前已能区分"已确认的语义调用"和
   "未确认的坐标合成"；应进一步在走了坐标兜底时明确提示"本次未使用元素定向，
   若树中存在该元素请优先用 element_index"
4. **让 a11y 通道在人体工学上更省事**：树里的 `element_index` 必须显眼、稳定、
   可直接引用；坐标反而应该更"费劲"一点
5. **不要在语义调用可用时把坐标并列呈现**——并列等于暗示两者等价

引导效果由"语义调用 vs 坐标兜底"的比例度量，纳入 S3 报告口径。
**注意区分两种低使用率**：agent 不想用（引导问题）与 agent 用了但失败后退化
（能力问题）。两者修法相反，必须分开统计。

### 裁剪与呈现
- **先做对照移植**：上表里 `shouldSkipChild` / `isPlainGenericTextContainer` /
  `placeholderValue` / `isSiblingCounterText` 直接对应裁剪需求，macOS 已有验证过的判据
- **再做研究**：macOS 没有回答的部分才需要实验（增量观测、扁平列表 vs 缩进树、查询式接口）
- 无论移植还是研究，都要先过保留率 / 压缩率离线评测

### 环境就绪度
- a11y readiness probe 收进仓库，作为环境自检入口
- 沉淀解锁配置清单（当前已知：Chrome/Electron 需 `--force-renderer-accessibility`
  且必须独立 `user-data-dir`，否则参数被现有会话交接吞掉）

### OSWorld 接入
- harness 跑通。优先评估在 OSWorld-MCP 的 `run_multienv_e2e.py` 之上改造（它已解决 MCP↔OSWorld 接线），
  而不是从零接入 OSWorld
- 选定首批任务子集（建议从 LibreOffice 起步：任务密度高、a11y 完整、已有修复积累）
- 产出 baseline 四元组（成功率 / 步数 / token / a11y 通道使用率）
- harness 观测策略：默认只给 a11y 树，VLM 事件驱动触发而非固定步数

## OSWorld 侧的既有事实（2026-07-30 查阅官方 repo）

查阅 `xlang-ai/OSWorld`（3045 star，2026-07-28 仍在提交）与 `X-PLUG/OSWorld-MCP` 后确认：

**技术栈与我们一致，X11 确认**
- 环境服务端 `desktop_env/server/main.py`（1797 行）用 **pyatspi**——和本 MCP 同一套 AT-SPI 栈
- 用 **Xlib**，全仓库 wayland 仅 1 处提及。X11 确认，与我们的基线环境一致
- a11y 树被序列化成带命名空间的 **XML**（`st:` 状态 / `cp:` 组件 / `val:` 值 / `act:` 动作等）

**a11y-first 是官方一等公民，但仅限观测**
- 观测空间：`a11y_tree` / `screenshot` / `screenshot_a11y_tree` / `som`
- 动作空间：`pyautogui` / `computer_13`
- **两个动作空间都只有坐标，没有元素定向入口**。`computer_13` 的 `CLICK` 参数是
  `button` / `x` / `y` / `num_clicks`；全仓库搜 `element_id` / `do_action` /
  `grab_focus` / `AtspiAction` 均 0 命中。唯一的 `ATAction`（`server/main.py:503`）
  只是把动作名读出来写成 XML 属性，从不调用 `doAction()`
- 结论：**OSWorld 里 a11y 只负责"看"，"做"仍然落到坐标**。
  a11y 树的作用是把坐标来源从"看图猜"换成"从树里读"，但坐标 grounding 并未被消除

### 这是本项目最重要的结构性差异

本 MCP 的 `element_index` → AT-SPI `do_action()` 链路上**完全没有坐标**。
这与 OSWorld 基线（坐标）和 OSWorld-MCP（per-app 语义工具）都不同，是第三个设计点。
坐标 grounding 是 VLM-first 的主要错误来源，而 OSWorld 的 a11y 观测模式并没有消除它。

**这个差异必须在评测里被单独体现**，否则容易被误读成"又一个 a11y agent"。
建议在四元组之外单独统计：语义调用 vs 坐标兜底的比例。

**官方已有裁剪启发式，与本计划的 H1/H3 完全一致**
`mm_agents/accessibility_tree_wrap/heuristic_retrieve.py` 的 `judge_node()` 做两件事：
1. **角色白名单**：只保留 document / item / button / heading / label / scrollbar /
   searchbox / textbox / link / textfield / textarea / menu 以及 entry / combo-box /
   table-cell / terminal / paragraph 等
2. **可见性过滤**：Ubuntu 侧要求 `showing=true` **且** `visible=true`

这意味着 H1（可见性过滤）与 H3（角色过滤）**不是待验证假设，是行业既有做法**，
且有参考实现。对本计划的影响：裁剪工作的下限从"可能有效"变成"至少要做到与官方持平"，
真正的差异化空间在 macOS 那套更精细的判据（`isPlainGenericTextContainer` 等）
以及 macOS 和 OSWorld 都没做的部分（增量观测、查询式接口）。

**OSWorld-MCP 已经验证了"MCP 工具能提升 computer-use agent"这一命题**
- ICLR 2026 接收，158 个工具覆盖 7 个应用，250 个 tool-beneficial 任务
- 报告数据：OpenAI o3 在 15 步设定下 **8.3% → 17.6%**
- 但**工具形态与我们不同**：它是 per-app 的语义工具（LibreOffice/VS Code/Chrome/VLC 各一套），
  我们做的是**通用 a11y 驱动**的 computer-use 接口。这是两个不同的赌注，不可直接互相印证
- 其 `run_multienv_e2e.py` + `osworld_mcp_client.py` 已经解决了 MCP↔OSWorld 的接线问题，
  接入时可考虑在其之上改造，而不是从零接。注意它最后提交于 2026-05-13，落后于 OSWorld 主线

**一个必须正视的风险：模型不一定会去用工具**
OSWorld-MCP 报告的最高 Tool Invocation Rate 仅 **33.3%**（Claude-4-Sonnet，50 步）。
也就是说工具做得好不等于会被调用。**"agent 是否愿意用 a11y 通道"本身就是一个要度量的指标**，
应纳入 S3 的报告口径。

## 研究议题：a11y 该怎么给 agent

这是本计划里最不确定、也最可能决定成败的部分。**先定评测方法，再试方案**，否则无法判断
某次裁剪是变好了还是丢了信息。

### 评测方法（先做这个）

离线、廉价、可重复：录制若干条人工或 agent 完成任务的轨迹，记录**每一步实际操作的元素**。
然后对任意裁剪方案，检查两个数：

- **保留率**：被实际操作过的元素，有多少在裁剪后仍然存在且可寻址
- **压缩率**：裁剪后 token 相对原始的比例

理想是保留率 100% 且压缩率尽量低。任何让保留率 < 100% 的方案都要单独审视丢了什么。

### 待验证假设

| 编号 | 假设 | 预期收益 | 风险 |
|---|---|---|---|
| H1 | 只保留屏幕可见节点，不丢任务关键元素 | GTK 系 ~88% 压缩 | 未展开菜单项被裁掉后，agent 不知道功能存在。**注：OSWorld 官方 `judge_node()` 已这么做（要求 showing 且 visible），此项已是既有做法而非待验证假设** |
| H2 | 扁平索引列表比缩进树更省 token | 去掉缩进与重复层级 | 丢失层级语义，可能影响定位判断 |
| H3 | 过滤纯结构性容器（filler/panel/separator） | 中等 | 少数容器本身可点。**注：OSWorld 官方用角色白名单达到同等效果，可直接参照** |
| H4 | 增量观测：只给相对上次的变化 | 多步任务收益最大 | 需要稳定的元素标识；agent 需能请求全量 |
| H5 | 菜单等层级按需展开，不预先枚举 | 直击 LibreOffice 的虚高 | 多一轮交互 |

H1 有最强的数据支持。H4 潜在收益最大，但依赖元素标识跨观测稳定（见清单 #15）。

### 需要一并想清楚的呈现问题

- 元素标识：`element_index` 跨观测是否稳定？不稳定则 H4 无法成立
- 状态表达：选中、禁用、展开与否，这些当前没有在树里体现，但对决策有用
- 截断策略：超预算时应该丢什么？当前是按遍历顺序截断，等于随机丢
- 是否需要给 agent 一个"查询"而不是"转储"接口（按名字/角色找元素），从根上回避树的大小问题

## 跨平面边界（浏览器）

浏览器由 browser-use + Playwright 控制，不走本 MCP 的 AT-SPI 路径。必须约束：

- Playwright 必须 `connect_over_cdp` 接管**环境里那个 Chrome**，不能自己 launch。
  OSWorld 部分验证器会检查 Chrome profile（书签、历史、下载、标签页），
  独立实例会让验证器什么都查不到，动作再正确也判 0 分
- 路由规则必须显式写进 system prompt：Chrome 归 Playwright，其余归本 MCP。
  否则 agent 会尝试用 AT-SPI 操作 Chrome 并反复试错
- 交接点是下载目录：浏览器下载 → `~/Downloads` → GUI 应用打开。这类跨平面任务最易碎，需单独测

## 如何把这个 plan 交给 agent 执行

这份 plan 是跨会话的载体——agent 的上下文会丢，plan 不会。所以**每轮开工的 prompt 不要复述
计划内容**（会漂、会和文件冲突），而是指向它，再补上那些"上下文丢了也必须守住"的约束。

### 每轮开工 prompt（照抄，替换尖括号部分）

```text
读 docs/exec-plans/active/20260730-linux-a11y-first-osworld.md，这是本项目的目标、
推进顺序和已有结论。先读完再动手。

本轮只做：<待办清单里的一项，例如 #1 LibreOffice 菜单->对话框链路实测>

不可违反的约束：

1. 任何"修好了/生效了"的结论必须有独立证据——AT-SPI 真值读数、测试输出、
   before/after 对比。工具自己返回 isError=false 不算证据，那正是本项目在修的
   bug 类型（动作返回一棵新的 accessibility tree，看着像执行确认，其实只是快照）。

2. 每个回归修复都要配一个"在修复前失败、修复后通过"的测试，并且要真的拿改动前的
   代码跑一遍确认它失败。只验证"改完能过"证明不了任何事。

3. 测量前先确认被测对象是活的。本项目踩过两次：应用退出后 AT-SPI 里残留僵尸注册，
   按名字查会得到 2 节点空壳；带新参数启动 Chrome 会被现有实例交接、参数完全失效。
   两次都得出了完全错误的结论。

4. 改完就提交，commit message 写清为什么这么改。不要攒一大坨。

5. 代码变更在 docs/histories/ 留记录；行为变更同步 docs/ARCHITECTURE.md；
   plan 的进度记录和决策记录同步更新。

6. 遇到不确定：先把不依赖它的部分做完，再把问题一次问清。不要停在那儿等，
   也不要自己假设一个答案就往下走。

7. 做不到的部分要明说。不要缩小范围然后当作完成了。

完成标准：待办清单里该项的**验收标准**（每项都写好了，直接抄），
且以下全部通过并已提交：
  (cd apps/OpenComputerUseLinux && go vet ./... && go test ./... && python3 -m unittest runtime_test)
  scripts/verify-linux-input-chain.py --app <目标应用>
```

### 为什么是这几条

七条约束全部来自本项目实际发生过的失误，不是通用模板：

- 第 1 条对应本计划要解决的核心问题本身。如果 agent 自己也用"返回了就是成功"来判断，
  它修不了这个 bug，因为它看不见这个 bug
- 第 2 条：本轮 22 个回归用例都做了"改动前跑一遍确认失败"，其中 5 个断言失败、
  13 个功能缺失。没有这一步就无法区分"测试有效"和"测试恰好通过"
- 第 3 条：僵尸注册和浏览器会话交接各骗了一次，都导致了错误结论并写进了汇报，
  事后才推翻
- 第 6 条：OSWorld harness 状态未知时，可靠性/双轨/对齐 macOS 全部照常推进，没有阻塞

### 关于 goal 的归属

技术判断（哪里坏了、最便宜的杠杆是什么、什么顺序做）可以由 agent 基于实测提出并承担。
但以下属于人的决定，agent 只能提默认值并标注出来，不能替你拍板：

- 是否追求 OSWorld leaderboard 可比性（越晚改成本越高，会反向约束动作空间设计）
- 观测成本的真实预算
- 范围边界（例如 shell 捷径算不算"符合 benchmark 精神"）
- 首批目标应用

当前这四项都已由 agent 给了默认值并写在决策记录里，标注为"可随时推翻"。


## 浏览器控制平面的落地状态（2026-07-30 实测）

**Playwright CDP attach 已验证通过**，脚本沉淀在 `scripts/verify-browser-cdp-attach.py`。
验证的不是"Playwright 能跑通"，而是从外部独立证明操作的就是环境实例：

- 接管后能看到环境实例的 context / page
- 通过 Playwright 导航后，**环境实例的窗口标题跟着变**（用 `wmctrl` 独立观测，
  不采信 Playwright 自己的返回值）
- 全程 Chrome 进程数 11 → 11 → 11，没有第二个浏览器被拉起
- 断开 CDP 后环境实例仍存活

这条验证直接消除了本计划标记的最大浏览器风险（Playwright 自己 launch 导致
OSWorld 验证器查不到任何东西、静默判 0 分）。

**browser-use 也已接入并通过同一套判据**（browser-use 0.13.7）。接口是
`Browser(cdp_url=...)`，行为与 Playwright 一致：接管环境实例、窗口标题跟随导航变化、
不另起浏览器、断开后环境实例存活。

两个落地约束：

1. **Python 版本**：browser-use 要求 `>=3.11,<4.0`，而环境是 3.10.12，
   Ubuntu 22.04 默认源没有 python3.11。解决方式是用 `uv` 装一个用户级独立
   Python（`~/.venvs/browseruse`，Python 3.12.13），不动系统 Python、无需 sudo、可逆。
   注意这个约束来自 browser-use 而非 OSWorld（后者只要求 ≥3.10），
   所以 browser-use 只能跑在宿主侧，不能假定 guest 里能装。
2. **默认拦截 `file://` 导航**：browser-use 的 SecurityWatchdog 会阻止本地文件
   导航并抛 `Navigation to file://... blocked by security policy`，且被拦后事件总线
   会持续重试导致调用方挂起。**OSWorld 有相当一批任务涉及本地文件**
   （下载后打开、处理桌面上的图片等），这条必须在接入前确认放行策略，
   否则会以"超时"而非"被拒绝"的形式暴露，很难排查。

## OSWorld 实际需要的操作类型（2026-07-30 抽样官方任务）

抽取 8 个 domain 各 4 条真实 instruction 后的归纳：

| domain | 代表任务 | 主要操作类型 |
|---|---|---|
| libreoffice_writer | 首两段改双倍行距；把 H2O 的 2 改成下标；页脚加页码 | **菜单 → 对话框 → 控件设置** |
| libreoffice_calc | 空单元格向上填充；新建 Sheet2 并算年度百分比变化；插入柱状图 | 单元格编辑 + 菜单 + 对话框 |
| libreoffice_impress | 文本框改色；各幻灯片文本对齐方式不同；按内容改背景色 | 选中对象 + 菜单 + 对话框 |
| vlc | 关闭启动画面 cone 图标；阻止播放结束自动关闭 | 偏好设置对话框 + 复选框 |
| os | 终端尺寸持久化；递归复制 .jpg；调最大音量；按修改时间压缩文件 | **大部分可用 shell 完成** |
| thunderbird | 消息过滤器；深色模式；添加 outlook 账号；设置纯文本签名 | 多步对话框 + 表单填写 |
| gimp | 转 CMYK；转索引色；背景透明；批量调亮度 | 菜单 → 对话框 |
| vs_code | 从 vsix 安装扩展；全文替换；设置换行列宽 | 命令面板 + 设置界面 |

**关键归纳：主导操作类型是「菜单导航 + 对话框交互」，而不是自由点击。**
这对 a11y 路线是好消息——菜单项和对话框控件恰恰是 AT-SPI 语义动作暴露得最好的部分，
`element_index` -> `do_action()` 天然适配；反而是自由画布类操作（GIMP 的部分任务）
才需要坐标。

`os` 这一类里相当一部分用 shell 完成又快又稳，这支持"Claude Code 自带 shell"
作为效率杠杆的判断，但是否符合 benchmark 精神由人判断（见决策记录）。

**下一步**：按这张表逐类在本机实测——先打 LibreOffice 的「菜单 → 对话框」链路，
因为它同时是任务密度最高和最能验证语义动作能力的一类。

## 风险

- 风险：裁剪丢失任务关键元素，成功率下降而不自知
  - 缓解：先建保留率评测（清单 #7），任何裁剪方案先过离线评测再上线
- 风险：抢焦点打断共享桌面的使用者
  - 缓解：OSWorld 场景 agent 独占桌面，代价可接受；已记入 `docs/ARCHITECTURE.md`
- 风险：语义动作空间与 OSWorld 官方 pyautogui 动作空间不同，分数不可比
  - 缓解：接受不可比，报告口径固定为四元组并注明设定（见决策记录）
- 风险：snap 打包的应用无法接入 a11y
  - 缓解：环境层面改用 deb/flatpak，或该应用降级走 VLM。运行时无解
- 风险：在缺陷未清理干净的 runtime 上接 OSWorld，量到的是 bug 不是能力
  - 缓解：可靠性项做完并有回归防护后再接 OSWorld（清单 #25）

## 验证方式

- 命令：
  - `(cd apps/OpenComputerUseLinux && go test ./... && python3 -m unittest runtime_test)`
  - `scripts/verify-linux-input-chain.py --app <app>`
  - `scripts/a11y-readiness-probe.py`（应用 a11y 就绪度与观测成本）
  - `scripts/verify-browser-cdp-attach.py`（确认 Playwright 接管的是环境实例）
- 手工检查：
  - 首批 OSWorld 子集的逐任务轨迹回放
- 观测检查：
  - 每次改动同时报告成功率 / 平均步数 / 平均 token，缺一不可

## 已完成

按时间顺序，每项都有对应的 history 或脚本可查：

- [x] 修复输入链路两处静默失败（选错可编辑控件、全局合成误投），补齐回归防护
- [x] `type_text` 改为 caret 插入 + 选区替换；`set_value` 回读确认；过滤 INT_MIN 哨兵坐标
- [x] 动作结果附执行路径与是否已确认；复用缓存快照做无变化检测
- [x] 10 个应用的 a11y 就绪度实测，产出基线表与观测成本构成 → `scripts/a11y-readiness-probe.py`
- [x] 查阅 OSWorld / OSWorld-MCP 官方实现，确认 X11、观测/动作空间、官方裁剪启发式
- [x] 查证"僵尸 AT-SPI 注册"——不存在，原诊断错误，已撤销并写明原委
- [x] 修复"空壳 a11y 静默成功"（活应用只暴露窗口框时给出可执行诊断）
- [x] 抽样 OSWorld 8 个 domain 的真实任务，归纳所需操作类型
- [x] 接入 Playwright + browser-use，验证均为 attach 而非 launch → `scripts/verify-browser-cdp-attach.py`
- [x] #1 LibreOffice 菜单→对话框链路实测；修复模态对话框对 agent 不可见
- [x] #1b combo box 路径攻关：定位到 combo 是 INT_MIN 幻影、真实控件是 toggle button；
      下拉弹窗已可见，但提交仍无解（详见实测发现小节）
- [x] #23 补齐 11 个仓库卫生文件，`./scripts/ci.sh` 现已完整跑通
- [x] #4 观测双轨拆分：`get_app_state` 不再带图（2922 → 1908 token），
      新增 `get_screenshot` 作为 VLM 轨唯一入口

## 实测发现：LibreOffice「菜单 → 对话框」链路（2026-07-30，待办 #1）

在 Writer 上对 `格式 → 段落 → 行距 → 双倍` 全链路实测，每步用 AT-SPI 真值独立判定。

**能走通的（全部纯 `element_index` 语义调用，零坐标）**

| 环节 | 结果 |
|---|---|
| `menu Format` → 展开 | 通 |
| `menu item Paragraph...` → 打开对话框 | 通 |
| 对话框可见 | 通（**需先修模态窗口优先，见 history**）|
| `spin button` 的 `set_value` | 通，且回读确认生效 |
| `push button OK` 语义点击 | 通 |

**对话框里的 combo box（行距选择器）：导航能走通，提交走不通**（待办 #1b）

先是一个结构性发现：**树里那个 `combo box` 节点是幻影**——它的 extents 是
`-2147483648,-2147483648 1x1`（INT_MIN 哨兵），根本没有渲染。屏幕上真正的控件是
它旁边的 **`toggle button`**。此前对 combo box 的所有操作都打在虚空里，
`press` 返回 `True` 纯属假成功。

改打 `toggle button` 后，**导航链路完全打通**：

- `do_action("click")` 到 toggle → 下拉作为**独立顶层 `window`** 弹出
  （状态 `SHOWING, VISIBLE, MODAL, ACTIVE`），而不是 combo 的子节点
- 该弹窗与 Paragraph 对话框**同为 MODAL**，靠 `ACTIVE` 区分最上层（已修）
- 弹窗内是带 `Selection` 接口的 `table`，渲染为 `cell R3C0 Double` 等 8 个选项，
  agent 完全可见（对方的 MANAGES_DESCENDANTS 坐标寻址兜底在这里正确生效）

**但没有任何一条路径能把选中真正提交下去**，`line-height` 始终停在 `100%`：

| 路径 | 结果 |
|---|---|
| `do_action` 到 `cell Double` | 返回 True，下拉关闭，值未应用 |
| `Atspi.Selection.select_child(table, 4)` | 返回 True，值未应用 |
| 裸 xdotool 方向键 + Return | 按键没进弹窗，下拉都没关 |
| MCP `press_key`（带夺焦点） | 同样无效 |
| 写 combo 的 `text` 兄弟节点 | `set_value` 成功且回读确认值变成 "Double"，文档未变 |

根因线索：**AT-SPI 说弹窗是 `ACTIVE`，但 `xdotool getwindowfocus` 显示 X 输入焦点
仍在主窗口上**——两个信号打架，键盘因此永远送不进弹窗。这也解释了夺焦点为什么没用：
`focus_window` 抓的是 AT-SPI 层的焦点，改变不了 X 层的输入焦点归属。

**结论**：LibreOffice 的下拉选择目前无法通过语义路径提交。导航部分（打开下拉、
看见全部选项）已经可用，缺的是最后一步提交。

**由此暴露的一个验证盲区**：第 3 条里 `set_value` 的"回读确认已生效"是**诚实但不充分**的——
它确认的是**控件的值变了**，不是**应用真的采纳了这个值**。对于会把控件值和文档状态分开的
应用（对话框类几乎都是这样），需要更强的判据。

**可用的 ground truth**：`Atspi.Text.get_default_attributes(text_iface)` 返回
`line-height` 等段落级属性，可直接判定格式类任务是否真的生效。
注意 `Atspi.Text.get_attributes()` 在此版本不存在，`Accessible.get_attributes()`
只给 `level` / `heading-level`，都不能用。

**同批发现的其它问题**

- **命名歧义**：子串匹配 `Format` 会同时命中 `menu Format`、`check menu item Formatting Marks`、
  `menu Formatting Mark`、`menu item Clone Formatting`。必须角色 + 精确名才能可靠定位，
  agent 面临同样的消歧成本
- **菜单展开状态不可见**：点开菜单后 `EXPANDED` 状态为空，只能靠重读树发现多了菜单项
- **对话框控件普遍无名**：行距 combo 在树里就是 `32 combo box`，没有名字也没有当前值，
  只能靠父节点 `panel Line Spacing` 推断

## 待办清单（完整，不分阶段）

一份完整的剩余工作。**没有阶段划分**——除标注了依赖的项外都可以并行推进。
每项的"验收"可直接抄进开工 prompt 的完成标准。

### 可靠性与动作能力

**#1 LibreOffice 菜单 → 对话框链路实测** ✅ 已完成　依赖：无
> OSWorld 的主导操作类型，任务密度最高，也最能验证语义动作的真实能力。
- 验收：用 `element_index` 语义调用走通 `格式 → 段落 → 行距 → 双倍`，
  并用 AT-SPI 真值确认行距**真的改了**；失败点列成清单

**#1b combo box / 下拉选择的可用路径** ⚠️ 部分完成（导航通、提交无解）　依赖：无
> #1 实测发现 combo box 四条路径全不通，而下拉选择在 OSWorld 对话框里极其常见。
- 验收：找到至少一条可靠路径（键盘序列 / Selection 接口 / 其它），
  并用 `line-height` 类 ground truth 确认应用真的采纳了值；找不到则明确记录为
  必须走 VLM 的操作类型

**#1c 区分"控件值变了"与"应用采纳了"**　依赖：无
> `set_value` 目前回读控件值即判成功，但对话框类应用会把控件值与文档状态分开。
- 验收：给出更强的判据，或在 Note 里明确标注该确认的边界

**#2 9 应用 × 7 动作系统性排查**　依赖：#1
- 验收：产出 9×7 失败面矩阵，每格 PASS/FAIL 且附 AT-SPI 真值证据

**#3 `click` / `press_key` 的效果判据**　依赖：#2
- 验收：能区分"生效"与"送达但无响应"；有前失败后通过的回归测试

**#4 观测双轨拆分** ✅ 已完成　依赖：无
- 验收：a11y track 返回**不含 image block**；截图有独立入口；
  gedit 单次观测从 2922 token（1908 文本 + 1014 截图）降到约 1900

**#5 对照 macOS 补齐 9 项能力缺口**　依赖：无
- 验收：缺口表里 9 个"缺失"项逐条有结论——**已移植** 或 **判定不可搬 + 理由**，不留空

**#6 引导 agent 走 a11y 通道** ✅ 已完成　依赖：#4
> 三处引导已就位，且比例可被机器统计：
> - **工具描述**：`click` 写明"PREFER element_index：可靠、不抢焦点"；
>   `get_screenshot` 写明"ONLY when the accessibility tree is insufficient"
> - **`serverInstructions`**：明确两条观测通道**不对等**，并写清切轨条件
>   与"不要为了看一眼就要截图"
> - **动作 Note 即时纠偏**：走坐标兜底时附加
>   "prefer click(element_index=...) — it is verified, cheaper, and does not steal focus"
>
> **通道标签**：每条动作 note 现在以 `[semantic]` 或 `[synthesis]` 开头。
> 这让"语义调用 vs 坐标兜底"的比例可以直接从 note 统计出来——它是 S3 报告口径
> 的第四项，也是区分"agent 不想用 a11y"（引导问题）与"用了但失败后退化"
> （能力问题）的唯一依据，两者修法相反。

### 裁剪与呈现

**#7 保留率 / 压缩率离线评测**　依赖：#8
- 验收：给定任意裁剪方案，能算出保留率与压缩率两个数

**#8 轨迹数据生成**　依赖：无
> 自驱动 agent 带本 MCP 跑任务，记录每一步**实际操作过的元素**。
- 验收：产出可复用的轨迹集，每步记录了被操作元素的稳定标识

**#9 移植 macOS 的裁剪判据**（`shouldSkipChild` / `isPlainGenericTextContainer` /
`placeholderValue` / `isSiblingCounterText`）　依赖：#5 #7
- 验收：保留率 100% 前提下，LibreOffice 观测 token 从 10.8k 显著下降

**#10 H1 可见性过滤**　依赖：#7
- 验收：给出 GTK 系应用的实际压缩率与保留率，判定是否采纳

**#11 H2 扁平索引列表 vs 缩进树**　依赖：#7
- 验收：两种渲染在同一批轨迹上的 token 差与保留率差，给出取舍结论

**#12 H3 结构性容器过滤**　依赖：#7（部分由 #9 覆盖）
- 验收：同上，且确认没有裁掉本身可点的容器

**#13 H4 增量观测（只给相对上次的变化）**　依赖：#7 #15
- 验收：多步任务上的累计 token 降幅；且 agent 能请求回退到全量

**#14 H5 菜单等层级按需展开**　依赖：#7
- 验收：LibreOffice 的"可交互虚高"是否被消除；多出的交互轮次是否可接受

**#15 `element_index` 跨观测稳定性** ✅ 已完成　依赖：无
> #13 的前提。实测结论：**索引是位置性的，不是身份稳定的**。

| 场景（gedit，241 元素） | 索引漂移 |
|---|---|
| 空转两次观测 | 0% |
| 无副作用按键（Home）后 | 0% |
| 插入文本后 | 0%（仅内容变化：脏标记 `*`）|
| **菜单展开（结构变化）** | **26%** |
| **菜单关闭后** | **仍 26%，不回弹** |

关键在最后一行：index 79 从 `radio button Documents` 变成 `filler` 后，
关掉菜单**也没有变回去**——结构一旦变过，编号就永久重排了。

对 #13 的影响：增量观测不能直接用 index 做身份，必须引入稳定标识
（`runtimeId` 路径已在 element record 里，可作为基础）。当前用法之所以安全，
是因为动作工具契约强制每次动作前重取 `get_app_state`，缓存不会过期。

**#16 状态表达（选中 / 禁用 / 展开与否）**　依赖：无
- 验收：这三类状态在树里可见，且不显著增加 token

**#17 截断策略**　依赖：无
> 当前超预算时按遍历顺序截断，等于随机丢弃。
- 验收：改为有优先级的截断，并说明"超预算时优先保留什么"

**#18 查询式接口评估**　依赖：无
> 给 agent 按名字/角色查元素的接口，从根上回避树的大小问题。
- 验收：给出该不该做的结论与理由；若做则有原型与 token 对比

### 浏览器控制平面

**#19 browser-use 的 `file://` 放行策略** ✅ 已完成　依赖：无
> 实测结论：**无法放行**。三种配置全部被 SecurityWatchdog 拦死：
> `allowed_domains=["file://*"]`、`allowed_domains=["*"]`、`disable_security=True`，
> 报错均为 `Navigation to file://... blocked by security policy`。
> 更麻烦的是被拦后事件总线持续重试，调用方表现为**超时**而非明确拒绝。
>
> 替代路径（OSWorld 有相当一批本地文件任务，必须选一条）：
> 1. 该类任务改走 **Playwright 直连**——已实测可正常导航 `file://`
> 2. 用本地 HTTP 服务中转本地文件
>
> 建议选 1：本项目已同时接入两者，按任务类型路由即可，不必额外起服务。

**#20 跨平面交接测试**　依赖：无
> 浏览器下载 → `~/Downloads` → GUI 应用打开。这类任务最易碎。
- 验收：至少一条完整交接链路端到端跑通并有证据

**#21 路由规则写进 system prompt** ✅ 已完成　依赖：无
> `serverInstructions` 现在显式写明：浏览器不由本 MCP 操作，Chrome/Chromium 归
> 独立控制平面（Playwright/browser-use over CDP）；浏览器之外的应用归本 MCP；
> **两个平面的交接点是文件系统**（下载落 `~/Downloads` 再用本 MCP 打开）。
> 并说明了为什么不能碰：Chrome 的 a11y 默认关闭，对它调 `get_app_state` 只会
> 在一个看不见的应用上烧回合。Go 侧有断言测试守住这三段措辞。

### 环境与仓库

**#22 沉淀 a11y 解锁配置清单** ✅ 已完成　依赖：无

环境搭建时按此清单配置，`scripts/a11y-readiness-probe.py` 可用于验收：

| 项 | 配置 | 说明 |
|---|---|---|
| GTK 系（gedit / Nautilus / GIMP） | `GTK_MODULES=gail:atk-bridge`；`toolkit-accessibility=true` | 本机已默认开启 |
| Qt 系（VLC） | `QT_ACCESSIBILITY=1` | 本机已默认开启 |
| **Chrome / Chromium** | `--force-renderer-accessibility`，**且必须配独立 `--user-data-dir`** | 不给独立 profile 的话，带新参数的启动命令会被现有实例**会话交接**走、参数完全失效，表现为"加了参数也没用" |
| **Electron（VS Code）** | `--force-renderer-accessibility` | 解锁后 602 节点可用，但观测成本高达 21.3k token |
| **snap 打包的应用（Firefox）** | 无解 | 日志明示 `Not loading module "atk-bridge"`，snap 封装接不上会话 a11y 总线。只能换 deb/flatpak，或该应用降级走 VLM |

排查时注意两个会导致误判的陷阱（已写进 probe 脚本头部）：
**僵尸 AT-SPI 注册**（应用退出后残留 app+frame 空壳）与
**浏览器会话交接**（新参数被现有实例吞掉）。

**#23 修复 `make ci` 跑不到底** ✅ 已完成　依赖：无
> `check-repo-hygiene.sh` 缺 `.editorconfig`、`.github/workflows/ci.yml` 等 11 个文件，
> 这在本计划开始前就是失败的。CI 守不住，记录质量迟早滑坡。
- 验收：`./scripts/ci.sh` 全绿

**#24 `insert_text` 追加 vs caret 的跨平台分歧** ✅ 已完成　依赖：无
> 结论：**记录为有意分歧，不强行对齐**，理由写入 `docs/ARCHITECTURE.md`。
>
> Linux 侧改 caret 插入是对的：它走的是 `Atspi.EditableText.insert_text(offset, ...)`，
> 本来就按偏移量写入，取 caret 偏移与取末尾偏移**成本完全相同**；而 agent 常常
> 先用 `click` 定位光标再调 `type_text`，追加到末尾会直接作废它刚做的定位。
>
> macOS 侧受 `AXValue` 整体读写的约束（读出全部内容、拼接、整体写回），
> 改成 caret 语义的代价与风险都高得多。若后续要统一，应以 caret 语义为准。

### OSWorld 接入

**#25 评估在 OSWorld-MCP 之上改造 vs 直接接 OSWorld**　依赖：#1–#6 基本完成
> 注意 OSWorld-MCP 最后提交于 2026-05-13，落后 OSWorld 主线约两个月。
- 验收：给出选型结论与理由，写进决策记录

**#26 harness 跑通并产出四元组基线**　依赖：#25
- 验收：LibreOffice 子集上产出 成功率 / 平均步数 / 平均 token / a11y 通道使用率

## 决策记录

- 2026-07-30：浏览器不走 AT-SPI，改由 browser-use + Playwright 独立控制。
  理由是 Chrome 的 a11y 需要额外解锁且树庞大，而 CLI/CDP 路径更稳、更可控。
  代价是引入第二个控制平面，需要显式路由规则和交接点测试。
- 2026-07-30：不追求与 OSWorld 官方 leaderboard 的可比性。
  本 MCP 提供的是语义动作空间（`element_index` + AT-SPI action），
  与官方 pyautogui 动作空间不同，分数天然不可比。
  真实目标是"用 Claude Code 高效操作桌面"，OSWorld 是尺子不是目的。
  若后续需要对外发布结果，此决策需重新评估。
- 2026-07-30：**推进顺序定为"缺陷优先、OSWorld 靠后"**，取代先前"baseline 是硬前置"的提法。
  baseline-first 只适用于优化类工作；缺陷修复的对错是自明的，不需要基线。
  在带着已知静默失败的 runtime 上量基线，量到的是 bug 不是能力，会污染后续所有对比。
- 2026-07-30：**a11y track 不提供截图**。a11y 优先，只有确实实现不了某个功能才切 VLM，
  而不是两者并行提供。理由是并行提供等于每次都付两份钱，a11y-first 的成本优势不成立。
- 2026-07-30：**裁剪与呈现优先走"对照 macOS 移植"，而不是从零研究**。
  Linux runtime 是 macOS 的移植且大量逻辑没跟上（13 项能力里 9 项缺失）。
  macOS 侧的判据已在真实应用上验证过，直接对照移植的风险远低于自己重新设计。
  仅对 macOS 没有回答的问题（增量观测、呈现形态、查询式接口）才做实验。
- 2026-07-30：**撤销"僵尸 AT-SPI 注册"这一判断**。严格复现表明 AT-SPI 在进程死后
  2 秒内即注销，不存在残留。原判断来自一次进程计数失误（真实进程名是
  `/opt/google/chrome/chrome`，而非 `google-chrome`）。
  现象本身是真的，但根因是"应用活着却没有可用 a11y"，已按真实根因修复。
  教训已写进 plan 的 agent prompt 第 3 条：测量前先确认被测对象的真实状态。
- 2026-07-30：**a11y 使用率是可引导的，不只是被度量的**。
  OSWorld-MCP 最高 TIR 仅 33.3%，说明"工具可用"到"工具被用"之间有很大落差，
  而这段落差主要由工具描述、server instructions 和动作返回内容决定，
  这些都在本 MCP 的控制范围内。故单列为待办 #6。
- 2026-07-30：**确认 OSWorld 的 a11y 仅用于观测，执行全部走坐标**。
  两个动作空间都没有元素定向入口，`ATAction` 只被用来把动作名读进 XML。
  因此本 MCP 的 `element_index` -> `do_action()` 是与 OSWorld 基线和
  OSWorld-MCP 都不同的第三个设计点，且是唯一真正消除坐标 grounding 的路线。
  这一差异必须在评测中单独体现。
- 2026-07-30：确认目标环境为 **X11**。OSWorld 环境服务端使用 Xlib + pyatspi，
  全仓库仅 1 处提及 wayland。与本计划的基线环境一致，Wayland 适配不纳入范围。
- 2026-07-30：**裁剪不再当作开放研究**。OSWorld 官方 `judge_node()` 已经在做
  角色白名单 + showing/visible 过滤，macOS 侧有更精细的判据。
  下限是"与官方持平"，差异化空间在 macOS 的精细判据和两边都没做的增量观测。
- 2026-07-30：**观测双轨拆分的优先级高于树裁剪**。
  实测发现 `build_snapshot()` 无条件截图，`get_app_state` 和所有动作工具的返回都带图，
  gedit 单次观测里截图占 35%。裁剪把文本砍掉 ~88% 之后，截图会升到约 80%，
  成为新的主要成本——先拆轨，裁剪的收益才兑现得出来。
