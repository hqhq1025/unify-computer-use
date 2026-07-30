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

**#1b combo box / 下拉选择的可用路径** ✅ 已找到可用路径　依赖：无
> **答案：下拉里的选项必须用坐标点击，`do_action` 不行。**
>
> 完整可用链路（`line-height` 实测由 `100%` 变为 **`200%`**）：
>
> | 步骤 | 方式 |
> |---|---|
> | `menu Format` | 语义 `do_action` |
> | `menu item Paragraph...` | 语义 `do_action` |
> | `panel Line Spacing` 下的 `toggle button` | 语义 `do_action`（打开下拉）|
> | **`table cell R3C0 Double`** | **坐标点击** ← 关键的一环 |
> | `push button OK` | 语义 `do_action` |
>
> 三个前提，缺一条这条路就走不通：
> 1. **不能点 `combo box` 节点**——它的 extents 是 INT_MIN 幻影，真实控件是旁边的
>    `toggle button`
> 2. **单元格必须带 Frame**——本轮修复。没有坐标就无从点击，这也正是之前五条
>    路径全部失败的原因
> 3. **需要 `OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS=1`**——坐标点击被
>    既有安全闸挡着（它会移动系统指针并改变前台焦点）
>
> 已失败且不必再试的路径：`do_action` 到 cell（关掉下拉但不提交）、
> `Atspi.Selection.select_child`（返回 True 不生效）、裸 xdotool 键盘、
> MCP `press_key`、写 combo 的 `text` 兄弟节点。
>
> ⚠️ **验证状态**：原始 AT-SPI 层的完整链路已证实（100% → 200%）；MCP 层每一步
> 单独验证通过（坐标点击 cell 返回成功、OK 在裁剪后的树里可见），但一次连贯的
> 端到端跑未能完成——LibreOffice 在密集自动化下反复退出。补一次完整的 MCP
> 端到端确认是这条的收尾工作。

**#1c 区分"控件值变了"与"应用采纳了"** ✅ 已完成　依赖：无
> `set_value` 原本说 `confirmed it applied`——这是**过度承诺**。回读确认的只是
> **控件的值变了**，不是**应用采纳了这个值**。实测证据：把行距 combo 写成
> "Double" 会回读成功，但文档的 `line-height` 纹丝不动，因为对话框把控件状态与
> 文档状态分开，只在 OK/Apply 时才提交。
>
> 改为明确说清确认的边界：
> - `set_value`：确认控件现在持有该值，但**这不等于应用采纳了**；对话框类控件
>   通常需要 OK/Apply，请用文档内容或重读元素来验证真实效果
> - `type_text` 直写：同样补上"若控件属于对话框，可能仍需 OK/Apply"
>
> Go 侧加断言测试守住新措辞，并**禁止旧的 `confirmed it applied.` 回潮**。
>
> 这条属于诚实性问题而非功能问题：过度承诺比不承诺更危险，
> 它会让 agent 停止验证。

**#2 逐应用打通（三层推进，不做全矩阵）**　依赖：#1

> **方法论（2026-07-30 定）**：不再按"9 应用 × 7 动作"铺全矩阵。全矩阵的问题是
> 把"这个应用能不能完成任务"和"这个应用每个控件都能不能点"混成了一件事，
> 结果是花大量时间在没人用的面板上，真正卡住任务的链路反而没走通。
> 改成三层，**严格按层推进，上一层没完成不进下一层**：
>
> | 层 | 做什么 | 何时做 |
> |---|---|---|
> | **L1 调研** | 从 OSWorld 真实任务文件里统计每个应用**实际需要**哪些操作，按频次排序 | 先做，可并行 |
> | **L2 打通必须操作** | 每个应用只测 L1 排出来的高频操作，逐条走通、遇错就修 | L1 之后，逐应用串行 |
> | **L3 穷尽面板功能** | 把每个应用所有面板、所有控件覆盖一遍 | **最后的最后**，L2 全部完成之后 |
>
> L2 的验收标准是**任务级**的（能不能完成一个真实任务），不是控件级的。
> 判定必须用外部真值（文件系统 / 窗口标题 / AT-SPI 真值读取），
> 不采信工具自己的 `isError`。

**#2a L1 调研：每个应用的必须操作清单**　依赖：无
- 数据源：OSWorld 官方 370 个任务文件（本地 `/home/user/OSWorld`）
- 产出：`docs/references/osworld-operations/*.md`，每个应用一份按频次排序的操作清单，
  每类操作标注"a11y 可寻址 / 可能只能坐标"
- 验收：清单里每条都能追溯到具体任务 id

**#2b L2 逐应用打通必须操作**　依赖：#2a
- 顺序：Nautilus ✅ → LibreOffice Writer/Calc → VS Code → VLC → GIMP → Thunderbird
- 每个应用的验收：至少完整走通 2 个真实任务级链路，全程外部真值验收；
  过程中发现的缺陷当场修掉并补前失败后通过的回归测试
- **LibreOffice Calc ✅ 高频操作已通关**（2026-07-30）：
  - **空单元格不再进树**：实测一张只有 3 列 4 行数据的表，视口 1081 个单元格里
    1069 个是空的，占掉 19971 / 23182 token（**86%**）。跳过后
    **23284 → 3780 token，降 84%**，12 个有内容的单元格一个不少。
  - **已验证**：`press_key` 移动单元格光标 + `type_text` 写入，内容确实落进
    光标所在格（树里出现 `table cell R3C0 Elderberry`）。
  - **未验证、已从提示里移除**：名称框跳转。`set_value` 能改它的文本却**不触发
    跳转**（控件变了、应用没照做，与下拉提交同一族）；`click` 也没能让它获得
    键盘焦点。最初的提示词写的就是这条，已改成只写实测过的路径——
    没验证通过的操作不写进给 agent 的提示。
  - **单元格输入 ✅ 判分器级验收**（28 个任务）：点单元格 → `type_text` → `ctrl+s`，
    **文件真值** `/tmp/cs.csv` 由 `Apple,3` 变为 `Fig,3`，时间戳更新。
  - **空单元格可达 ✅**（提示词声称的那条路）：点 R0C0 → 三次 `press_key Down`
    → `type_text`，内容落进 **R3C0**（原本为空、树里没有它的 element_index）。
    单次 Down 曾一度看似无效，实为时序——等待 0.6s 不够，0.7s 稳定。
  - **区域选择 ✅**（29 个任务）：`shift+Down` / `shift+Right` 扩展选区可用。
    **但选区不体现在单元格自身**——格子上没有 `selected` 标记，
    agent 只能从**名称框**（`Value: A1:B3`）或**状态栏**
    （`Value: Selected: 3 rows, 2 columns`）读出来。这两处都在树里，
    但需要知道去哪儿看，属于 a11y 表达与直觉不一致的地方。
  - 菜单导航与对话框改值（各 30 个任务）与 Writer 同源，已由行距链路覆盖。
- **LibreOffice Writer ✅ 头号阻塞已通关**（2026-07-30）：
  「全选 → 格式 → 段落 → 行距下拉 → Double → 确定 → 保存」全程走 MCP，
  **判分器级验收**：保存后的 `content.xml` 出现 `line-height="200%"`
  （初始无 line-height，即默认单倍）。
  通道分布：菜单/对话框/OK 走 semantic，下拉项走 auto 自动识别后的坐标点击，
  全选与保存走键盘。这条链路横跨 Calc/Impress/Writer ≥37 个任务。
  仍待做：Calc 的单元格与区域选择、Impress 的画布对象。
- **Thunderbird 🔶 设置链路已通关**（2026-07-30，XUL/Gecko）：
  - **判分器级验收**：AppMenu → Settings → 勾选「Auto hide tab bar」，
    `~/.thunderbird/*/prefs.js` 出现 `user_pref("mail.tabs.autoHide", false)`。
    （切回默认值时 Thunderbird 会删掉该行，所以来回切两次确认。）
  - **修掉两个 Gecko 特有的动作名问题**：
    1. `click ancestor`（**带空格**）——与 Chromium 的 `clickAncestor` 是同一
       语义的不同拼法。只排掉驼峰那种，带空格的照样被子串兜底匹中，
       16 个节点被误标成可点。判据已改为**归一化比对**（去掉大小写与分隔符）。
    2. `check` / `uncheck`——Gecko 用**结果状态**给复选框动作命名而非 `toggle`，
       任一时刻只暴露适用的那个，调用它就等于 toggle。不认这两个名字，
       Gecko 的复选框全部退回坐标点击，而设置类界面几乎全是复选框。
       修复后同一操作从坐标回落变成纯语义（0 次 synthesis）。
  - 仍待做：消息过滤器（3 个任务）、文件夹面板（5 个）、邮件列表批量操作（2 个）
    ——这几项需要真实账户，本机无网络账户。
- **GIMP 🔶 三项最高频操作已通关**（2026-07-30，GTK/GAIL）：
  - **菜单导航（17/17 任务）✅ + 模态对话框填参数（12/17）✅**：
    Colors → Brightness-Contrast → 设 Brightness=80 → OK，全程语义通道。
    **像素级验收**：画布采样 21875 个像素，21871 个发生变化（99%）。
  - **匿名 spin button 靠 description 消解**：该对话框两个 spin button
    **完全没有名字**，只有 `Description: Brightness` / `Description: Contrast`
    能区分。调研原本判定这里"需一次截图消解歧义"，本轮的 description 渲染
    直接把它解决在树里了。
  - **图层面板（5/17）✅**：`activate` 撒谎（返回 True 但活动图层不变），
    由本轮的自动回落接住，状态栏 `TopLayer` → `Background` 验证。
  - 仍待做：工具选择（3 个任务）、画布直接操作（3 个，调研判定必须依赖视觉）。
- **VLC 🔶 菜单与首选项已通关**（2026-07-30，Qt）：
  - **判分器级验收**：Tools → Preferences → 勾选 → Save，
    `~/.config/vlc/vlcrc` 由 `#qt-pause-minimized=0` 变为 `qt-pause-minimized=1`。
    **整条链路全部走语义通道**，一次坐标都没用——Qt 是四个工具包里语义执行
    最可靠的。
  - **Qt 的一个优势**：菜单项**不必先展开菜单**就能语义调用。子菜单项在菜单
    关闭时上报负坐标（`x: -70`），坐标点击必然失效，但 `do_action` 直接生效。
    这与 GTK 相反（Nautilus 的 `menu` 动作是假的，必须合成右键）。
  - **Qt 富文本 tooltip 已剥离**：Qt 把 tooltip 存成整段 HTML（含 `<head>` 里的
    CSS）。首选项对话框实测 19 段合计 9149 字符，占整次观测 **56%**。
    剥离后 4041 → 2261 token（-44%），描述变成干净的一句话。
    判据卡在 `<html>` 开头，不对普通文本动手——真实内容里可能有尖括号。
  - 仍待做：文件路径输入（6 个任务）、播放控制（5 个）、
    以及调研点名的 Simple/All 单选按钮（`Toggle` 后 CHECKED 翻转但面板不切换）。
- **VS Code ✅ 高频操作已通关**（2026-07-30，Electron/Chromium）：
  - **树是出来的**，不是空壳（`toolkit-accessibility` 本机已为 true）。
    欢迎页 9517 → 8393 token（修完动作名与 U+FFFC 之后）。
  - **修掉两个方向相反的动作名缺陷**（见当日 history）：`doDefault` 是
    Chromium 的默认动作却不被识别（19 个节点因此不可点）；`clickAncestor`
    含 "click" 被子串兜底匹中却**点的是祖先节点**（14 个节点，属静默点错对象）。
  - **编辑器内容可读，与调研结论需要修正**：调研说「Monaco 无行级节点，
    文本类任务只能纯键盘」。前半句对——没有逐行节点；但整份文档内容
    **完整暴露**在 `entry` 节点的 Value 里（实测读到
    `def add(a, b):\n    return a + b\n\nprint(add(2, 3))`）。
    所以**读是语义可行的，只有写要回落键盘**。
  - **命令面板可用**：`ctrl+shift+p` → `combo box input [expanded]`，
    带 `Placeholder: Type the name of a command to run.`，
    对它 `type_text(element_index=...)` 再回车，能打开
    `Preferences: Open User Settings (JSON)`。
  - **settings.json 确实被写入磁盘**（判分口径就是读这个文件），
    但内容不精确——`ctrl+a` 没能全选，残留了原文尾部。
  - **❌ 不要点 Monaco 编辑器的 entry 节点**：实测 `click` 之后 `[focused]`
    标记消失，编辑器**失去**键盘焦点，后续按键全部落空。
    可用的模式是：靠 VS Code 打开文件时自带的焦点，全程键盘，不要中途点击。
  - **原生模态对话框对 AT-SPI 不可见**：改完 settings.json 后 VS Code 弹出
    「A setting has changed that requires a restart to take effect.」，
    同进程、`_NET_WM_WINDOW_TYPE_DIALOG`、锁死整个应用，**树里完全没有它**。
    后果是 agent 看到正常的树、每个动作都被正确拒绝，却无从知道原因。
    已改进诊断：夺焦点失败时报出**实际持有焦点的窗口**；
    若无障碍树里没有任何 ACTIVE 窗口，直接指出"焦点被树看不见的东西拿着，
    去截图看看"。
  - **方法论教训（我自己犯的）**：前几轮我的测试脚本没有检查 `isError`，
    把工具的**正确拒绝**当成了执行成功，因而一路误判成"键盘没反应"。
    守卫本身完全正确。这正是"不采信工具自己的返回值"要防的反面——
    也不能忽略它明确报出的失败。
  - **✅ 文本替换判分器级验收**：`ctrl+1` → `ctrl+a` → `type_text` → `ctrl+s`，
    **磁盘文件** `/tmp/vsc-drill/main.py` 由 `def add(a, b): …` 变成
    `MULTIPLY = 42`（13 字节，时间戳更新）。
  - **✅ 焦点恢复配方：`ctrl+1`**（聚焦第一个编辑器组）。这是唯一实测有效的
    恢复手段——点编辑器节点会**丢**焦点，点大面积 section 也回不来。
    可靠模式：**全程键盘、中途不点击**；焦点一旦丢失就 `ctrl+1` 找回来。
  - 仍待做：6 个文件对话框任务。
- **Nautilus ✅ 已完成**（2026-07-30）：读状态 / 右键菜单 / 重命名 / 新建文件夹 /
  模态对话框 / 侧边栏导航六项走通，重命名与新建经文件系统验收、导航经 wmctrl 验收。
  期间修掉 5 个缺陷（侧边栏不可见、缩进错乱、description 未渲染、
  menu 动作撒谎、陈旧 element_index 静默点错控件）

**#30 LibreOffice 在密集自动化下反复崩溃**　依赖：无
- 现象：连续若干次 MCP 动作之后 soffice 进程消失，下次启动弹「文档恢复」。
  本轮至少发生 3 次，早前排查 #1b 时也遇到过，导致「下拉 → OK → 文档生效」
  这条链路始终没能一次跑完。
- 影响：LibreOffice 占 OSWorld 117/370 个任务，稳定性直接决定这部分的可测性。
  另外崩溃会留下恢复对话框，挡住后续所有操作——harness 需要能自动识别并 Discard。
- **已排查（2026-07-30），三个假设两个证伪、一个证实为无关**：

  | 假设 | 实验 | 结论 |
  |---|---|---|
  | 读树 + 按键的常规节奏太密 | 连续 30 次 get_app_state + press_key | **证伪**，30 次全过 |
  | 动作表被问两次（本轮 `[has-click-action]` 引入） | 合并为一次读，重测 | **证伪**，仍在第 3 轮消失 |
  | ATK 断言风暴（`impl_get_NActions`）拖垮进程 | 先判 Action 接口再问动作数，告警 3482 → **0** | **证实无关**，告警清零但崩溃照旧 |

- **区分实验定性**：开关对话框 **+ 下拉交互** 活 3 轮；**只开关对话框、完全不碰下拉**
  活 4 轮。两种都死——下拉只是让它更快到达临界点。
  **这是 LibreOffice 自身在 AT-SPI 驱动下反复开关模态对话框的脆弱性**，
  不是本 MCP 的下拉处理弄坏了什么。
  内核层面无 segfault / 无 OOM / 无 core dump，stderr 戛然而止，进程静默消失。

- **因此这不是 runtime 能修的，是 harness 必须具备的能力**：
  1. 识别应用消失（`appNotFound` 已加重试，但这里是真的没了）
  2. 自动重启并处理随之而来的「文档恢复」对话框（Discard → Yes，两级）
  3. 从任务的已知中间态续跑，而不是整个任务判失败
- 验收：harness 能在 LibreOffice 中途消失后自动恢复并续跑同一任务



**#2c L3 穷尽每个应用的所有面板**　依赖：#2b 全部完成
- **这是最后才做的事**。在 L2 没有全部走通之前不要开始。
- 验收：产出各应用的完整控件覆盖矩阵

**#3 `click` / `press_key` 的效果判据** ✅ click 已完成　依赖：#2b
- 验收：能区分"生效"与"送达但无响应"；有前失败后通过的回归测试
- 已有基础：`perform_secondary_action` 的开菜单类动作已实装"调用后校验 + 合成回落"
  （Nautilus 的 `menu` 动作实测永远返回 True 却永不生效），这条判据要推广到
  `click` 与 `press_key`
- **✅ click 已完成（2026-07-30）**：`actionResult` 在窗口标题/整棵树/焦点/选中
  全都逐行不变时，对同一元素自动重发坐标点击。端到端验证见 GIMP 图层面板——
  状态栏（独立于被操作节点）从 `TopLayer` 变为 `Background`。
  判据刻意不读被操作节点自身状态：VLC 单选按钮 `Toggle` 后 `CHECKED` 真的翻转了、
  面板却不切换。
- **已知边界**：该机制只接住"什么都没变"，接不住 VLC 那种"状态变了、行为没变"。
  后者需要知道动作**本该**造成什么后果，属任务级语义。
- **press_key 仍待做**：键盘合成没有元素锚点，判据与回落方式都不同，另议。

**#4 观测双轨拆分** ✅ 已完成　依赖：无
- 验收：a11y track 返回**不含 image block**；截图有独立入口；
  gedit 单次观测从 2922 token（1908 文本 + 1014 截图）降到约 1900

**#5 对照 macOS 补齐 9 项能力缺口** ✅ 已完成　依赖：无
> 缺口表里的每一项都有结论，不留空。结论分三类：**已移植**、**已用等价方案覆盖**、
> **判定不搬 + 理由**。

| macOS 能力 | 结论 | 说明 |
|---|---|---|
| `placeholderValue` | **已移植** | 本轮实装。AT-SPI 有对应的 `placeholder-text` 对象属性，实测在 gedit 上取到 `Search highlight mode…`。渲染为独立的 `Placeholder:` 段，**不混进 `Value`**——占位文本长得像内容但控件其实是空的，混在一起 agent 会以为已有值而跳过输入，或把提示语当数据读走 |
| `shouldSkipChild` | **已用等价方案覆盖** | #9 的裁剪（角色白名单 + 可见性）达到同一目的，且是 OSWorld 官方同源判据，实测 22% 压缩 / 100% 保留 |
| `isPlainGenericTextContainer` | **已用等价方案覆盖** | 同上。裁剪会丢掉无名的纯结构容器，但**保留有名节点**——名字是 agent 的定位依据（这条是踩过坑才加的，见 #9 的盲区记录） |
| `isUsableWindowElement` | **已用等价方案覆盖** | `main_window()` 的「可见模态 > ACTIVE > SHOWING」排序，加 `extents()` 过滤 INT_MIN 哨兵坐标 |
| `recoverVisibleWindow` | **已用等价方案覆盖，但机制不同** | macOS 可以 unminimize + AXRaise；AT-SPI 没有等价 API。实现为 `focus_window()`：对窗口内 FOCUSABLE 子控件 `grab_focus`（frame 自身的 grab_focus 在 GTK 上恒返回 False） |
| `preferredFocusedElement` | **已用等价方案覆盖，但机制不同** | macOS 读 `kAXFocusedUIElement`；AT-SPI 无此属性，改为按 `FOCUSED` > `SHOWING` 排序挑可编辑控件 |
| `shouldSuppressChildren` | **已覆盖** | `MANAGES_DESCENDANTS` 分支 + `HARD_CHILD_CAP`，另加菜单折叠（#14，实测折掉 726 项） |
| `meaningfulActions` | **已移植** | 对应 `CLICK_COVERED_ACTIONS` |
| `outlineRowSummary` / `flattenedRowTexts` | **部分覆盖** | 表格的坐标寻址已实现（`render_visible_cells`，带 Frame 和真实角色）；**行摘要未做**。当前表格按单元格逐个渲染，行级摘要属于进一步压缩，等有实际瓶颈再说 |
| `enableBestEffortAccessibilityModes` | **判定不可搬** | macOS 能在运行时设 AX 属性打开应用的 a11y；Linux 上这是**启动参数**（Chromium/Electron 的 `--force-renderer-accessibility`），进程起来之后无法开启。已改为沉淀成环境配置清单（#22），由环境搭建阶段负责 |
| `isSiblingCounterText` / `isStandaloneTimeRangeText` | **判定暂不搬** | 它们是 macOS「generic text container 压缩」的一部分，匹配 `3 / 10`、`12:00-13:00` 这类噪音串，主要受益场景是 **Web/WebView 深树**。本项目的浏览器走 Playwright 独立平面，不经 AT-SPI；原生应用里这类模式罕见。等出现实测噪音再补 |
| `markdownLinkText` | **判定暂不搬** | 依赖 `kAXURLAttribute` 渲染链接。同上，链接密集的场景是浏览器，而浏览器不走本 runtime |

**一条方法论上的收获**：逐条对照的价值不在于"照搬得越多越好"。12 项里真正照搬的只有 2 项，
6 项是用平台等价方案达成同一目的，4 项判定不搬。**照搬 macOS 的机制反而会出错**——
`kAXFocusedWindowAttribute`、`kAXFocusedUIElement`、运行时开启 a11y 这三样在 AT-SPI 上
都不存在，硬搬只会写出永远返回空的代码。对照的真正用途是**拿到它已经验证过的产品判断**
（什么该显示、什么该隐藏、什么算"可用窗口"），机制则必须按平台重新设计。

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

**#7 保留率 / 压缩率离线评测** ✅ 已完成　依赖：#8
> `scripts/evaluate-pruning.py`。吃 `record-trajectory.py` 录的轨迹，
> **离线**算两个数，不需要桌面会话，因此可反复跑、可进 CI，改一版裁剪就重算一次。
>
> 保留率的判据刻意**忽略 index**：裁剪会让编号重排，agent 关心的是
> "这个元素还在不在、还能不能按 role+name 找到它"。
>
> 内置 6 个策略，含**忠实移植的 OSWorld 官方 `judge_node()`**——
> 它用的是 `endswith("button")` 这类后缀匹配而非精确集合，第一版我写成精确集合，
> 结果把官方策略算成保留率 0%，属于给自己放水。改成忠实移植后结论完全反转。

**#8 轨迹数据生成** ✅ 已完成　依赖：无
> `scripts/record-trajectory.py`。不需要 LLM——把已验证可完成的操作序列脚本化，
> 跑一遍并逐步记录**当时的完整树**与**这一步实际操作的元素**。
>
> 每步记录：动作与参数、目标元素的**稳定标识**（完整渲染行，带 role/name/状态/Frame）、
> 完整树（lines + elements + raw）、以及带通道标签的 notes。
>
> 稳定标识不用 `element_index`：#15 已证明它是位置性的，结构一变就永久重排。
> 改用完整渲染行——这也正是 agent 实际定位元素的方式，比内部 id 更贴近
> 评测要回答的问题（"裁剪后还能不能找到它"）。
>
> 附带收益：`notes` 里的 `[semantic]` / `[synthesis]` 标签让同一批轨迹
> 可直接用于统计 S3 口径的第四项。
>
> 已内置三个场景：`gedit-type`（键盘+文本）、`gedit-menu`（元素定向点击）、
> `writer-line-spacing`（菜单→对话框）。实测录制正常，单条轨迹 30-60 KB。

**#9 裁剪判据落地** ✅ 已完成　依赖：#7 #5
> 原计划是移植 macOS 的 `shouldSkipChild` 等精细判据。#7 的实测改变了这个判断：
> **OSWorld 官方那套「角色白名单 + 可见性」已经拿到收益大头**，macOS 判据降级为
> 可选的增量叠加。
>
> 已实装进 `render_tree()`，**默认开启**，`get_app_state` 带 `prune=false` 逃生口。
> 被裁的节点只丢自己那一行，**仍然继续递归子节点**——中间容器往往正是有价值
> 控件的父节点。
>
> 真实成本（含 #4 的双轨拆分）：
>
> | 应用 | 原始 | 裁剪后 | 降幅 |
> |---|---:|---:|---:|
> | gedit | 2349 token | **620** | −74% |
> | LibreOffice Writer | 11241 token | **2608** | −77% |
>
> **S2 的 1.5k token 目标：中位数应用已达成，最差的 LibreOffice 降到 2.6k。**

**#9b 保留率指标的盲区（本轮暴露）**
> 离线保留率显示所有策略都是 100%，但**裁剪开启后真实链路当场断掉**：
> 行距 combo 的 toggle button **本身没有名字**，agent 只能靠父节点
> `panel Line Spacing` 指认它；而 `panel` 不在可交互角色白名单里被裁掉了——
> **目标元素还在树里，却没法被指认**。
>
> 保留率只检查"目标在不在"，不检查"定位它所需的上下文在不在"，这是指标的盲区。
> 修法：有名字的可见节点一律保留，哪怕角色不"可交互"——名字正是 agent 定位的依据。
> 修复后完整对话框链路 6 步全部走通。
>
> **教训**：离线指标只能证伪，不能证成。任何裁剪方案上线前必须跑一遍真实链路。

**#10 H1 可见性过滤** ✅ 已完成　依赖：#7
**#11 H2 扁平索引列表 vs 缩进树** ✅ 已完成　依赖：#7
**#12 H3 结构性容器过滤** ✅ 已完成　依赖：#7

实测（13 步轨迹 / **9 步元素定向**，含完整对话框链路，原始 49699 token）：

| 策略 | 压缩率 | 保留率 |
|---|---:|---:|
| 基线 | 100% | 100% |
| H1 只保留屏幕可见 | 42% | 100% |
| H3 丢无名结构容器 | 72% | 100% |
| **OSWorld 官方 judge_node** | **22%** | **100%** |
| H2 扁平列表（去缩进） | 80% | 100% |
| H1 + H2 | 37% | 100% |

**结论：采用 OSWorld 官方那套「角色白名单 + 可见性」**——22% 压缩率且**零保留率
损失**，是所有方案里唯一同时做到最激进和无损的。按此推算，LibreOffice 那
10.8k token 的观测可降到约 2.4k，与 S2 的 1.5k 目标已在同一量级。

### 这一轮暴露并修掉的两个渲染缺陷

**都不是裁剪策略的问题，是我们自己的渲染保真度问题。** 修之前，三种基于可见性
或角色的策略都会丢掉行距下拉里的 `Double` 选项——而它正是任务必须点中的元素。

**1. 坐标寻址渲染的单元格缺 Frame**
`render_visible_cells()` 渲染的单元格不带 Frame，而它们**恰恰是屏幕上真实可见的**
（就是坐标寻址取到的当前视口内容）。任何"只保留可见节点"的裁剪会把整个下拉/
表格判成不可见并全部丢弃。补上 Frame 后 H1 与 H1+H2 从 88% 回到 100%。

**2. 角色被硬编码成 `cell`**
真实的 AT-SPI 角色是 `table cell`。OSWorld 官方白名单里有 `table-cell` 却没有
`cell`，于是整批下拉选项被判为无关角色丢掉。改用节点真实角色后，官方策略从
88% 回到 **100%**。

这两个缺陷合起来说明一件事：**裁剪的上限由渲染的保真度决定**。渲染丢掉的信息
（可见性、角色），下游任何策略都补不回来。

### 两次把自己的对比做歪，都已修正

评测 OSWorld 策略时先后犯了两个错，方向都是**高估自己**：

1. 把官方的 `endswith("button")` 后缀匹配写成了精确集合匹配 → 算出官方保留率 0%
2. 解析器把无名的两词角色 `toggle button` 拆成 role=`toggle` + name=`button`
   → `endswith("button")` 判 False，又一次误伤官方

用更严的规则去比对手等于给自己放水。两次修正后，官方策略的真实表现是
**21% 压缩率 / 88% 保留率**，唯一失手处已定位为我们的角色保真度问题。

**#13 H4 增量观测（只给相对上次的变化）** ✅ 已完成　依赖：#7 #15
> 实测先推翻了"增量一定省"的直觉。用真实轨迹逐步比对（gedit）：
>
> | 轨迹类型 | 累计效果 |
> |---|---|
> | **有结构变化**（菜单开合） | **−7%，比全量还差** |
> | **无结构变化**（纯键盘/输入） | **省 62%**，其中若干步 diff 为 0 |
>
> 原因：结构一变，新增和消失两边都要付钱，加起来超过全量。所以不能无条件用增量。
>
> **判据是「行数不变」**，这一条同时解决了两个问题：
> 1. **成本**：行数不变意味着只有内容变了，diff 必然小于全量
> 2. **正确性**：#15 实测证明结构一变 `element_index` 就永久重排（gedit 上 26%），
>    而行数不变正是"无结构变化"的充分信号，此时索引 0% 漂移，
>    agent 可以安全沿用上一轮的索引
>
> **两个条件恰好对齐**——最省的场景正好也是最安全的场景，因此不需要额外引入
> 稳定标识就能成立。这是 #15 那个"看似坏消息"的结论带来的意外收益。
>
> 另加两道回退：完全没变化时不走增量（那属于"无可观测变化"提示的场景）；
> 变化超过三分之一时也不走（绕弯不如直接给全量好读）。
>
> **实测效果**（gedit，28 行树）：全量 672 token → 增量 **170 token，省 75%**，
> 表头明确告知"未变的行仍沿用同一个 element_index，需要全量就再调一次 get_app_state"。

**#14 H5 菜单等层级按需展开** ✅ 已完成（机制已在，本轮实测确认）　依赖：#7
> 机制已实现在 `render_tree()`：未展开的菜单保留节点自身（它是
> `perform_secondary_action` 的入口），但不递归其子项，并在树里留一行
> `(N items collapsed; activate this menu to expand)`。
>
> 实测收益（LibreOffice Writer，未裁剪模式）：**102 处折叠，共折叠掉 726 个菜单项**。
> 当前树 868 节点；不折叠的话约 1594 节点——**折叠砍掉了近一半的树**。
>
> 展开路径已在 #1 验证：点击 `menu Format` 后 `menu item Paragraph...` 出现在树里，
> 整条菜单→对话框链路走通。多出的一轮交互是值得的：这 726 项里 agent 每次
> 真正需要的只有一两个。

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

**#16 状态表达（选中 / 禁用 / 展开与否）** ✅ 已完成　依赖：无
> 树里的元素行现在会带紧凑状态标记，例如：
> ```
> 38  page tab  [selected]
> 48  text  [focused]
> 79  radio button Documents [checked]
> 135 push button Clear Highlight [disabled]
> ```
> 只渲染**非默认的一侧**：每个节点都标 `enabled` / `not-focused` 只会淹没信号，
> 而 `disabled` / `checked` / `expanded` / `selected` / `focused` 才是决策依据。
> `disabled` 尤其有用——此前 agent 会对着禁用控件反复点击而毫无察觉。
>
> 成本实测（gedit 241 节点）：11 个节点带标记（4%），
> 总量 7633 → 7756 字符，**+31 token（1.6%）**，符合"不显著增加 token"的验收。

**#17 截断策略** ✅ 已完成　依赖：无
> 原实现是深度优先切断：`len(records) >= max_tree_nodes` 就 return，
> 等于按遍历顺序随机丢弃——先到的占满配额、后面的整片消失，
> 而且 agent **完全不知道树被砍过**。
>
> 现在两件事：
> 1. **有优先级**：预算用到 80% 后开始丢"无名 + 无动作 + 无值"的纯结构容器
>    （`filler` / `panel` / `separator`），但**仍然继续递归它的子节点**——
>    被丢的容器往往正是有价值控件的父节点。
> 2. **不再静默**：树尾显式说明丢了多少、丢的是哪一类，并提示可以调大 `max_tree_nodes`。
>
> 实测有用节点密度（gedit）：预算 60 时 78% → **85%**；预算 120 时 65% → **75%**。

**#18 查询式接口评估** ✅ 已完成（结论：暂不做）　依赖：无
> 问题是：要不要给 agent 一个"按名字/角色查元素"的接口，从根上回避树的大小问题。
>
> **结论：暂不做。** 判断依据是成本已经降下来了——LibreOffice Writer 从
> 11241 token 降到 **2608**（菜单折叠 #14 折掉 726 项、裁剪 #9 再砍一轮），
> 观测也不再带那 1014 token 的截图（#4）。
>
> 树已经小到"转储"完全可接受，而查询接口有一个不可忽视的代价：
> **agent 会失去发现能力**。转储回答的是"这里有什么"，查询只能回答
> "我猜的这个在不在"——对不熟悉的应用，前者才是它能推进的前提。
>
> **重新评估的触发条件**（写清楚，免得靠感觉重开）：
> 1. 出现裁剪后仍 > 5k token 的应用（当前最差 2.6k）
> 2. 或实测发现 agent 大量把预算耗在"翻树找元素"上
>
> 满足任一条再做，届时应做成**转储 + 查询并存**，而不是用查询取代转储。

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

**#20 跨平面交接测试** ✅ 已完成　依赖：无
> `scripts/verify-cross-plane-handoff.py`。实测链路：
> **浏览器下载 → `~/Downloads` → GUI 应用打开 → a11y 可读**，三项全 PASS。
>
> 判据全部用外部观测，不采信任一平面自己的返回值：文件系统检查、窗口标题、
> AT-SPI 真值。
>
> 过程中踩到三个坑，每个都会让交接静默失败：
>
> 1. **CDP 驱动的导航没有用户手势，Chrome 会静默拦下下载**——不报错、不弹提示、
>    不留半成品文件，表现为"下载就是没发生"。必须用
>    `Page.setDownloadBehavior({behavior: "allow", downloadPath: ...})` 显式放行。
>    **这是本项最关键的发现**：不知道这条，跨平面任务会以最难排查的方式失败。
> 2. **Playwright 的 `expect_download` 对 CDP 接管的浏览器不可靠**——context 不是
>    它创建的，`acceptDownloads` 没配上。改成让 Chrome 原生下载再轮询文件系统，
>    这也更贴近真实场景。
> 3. **不加 `Content-Disposition: attachment` 的话 Chrome 会内联渲染 `.txt`**，
>    根本不触发下载，测的就不是交接链路了。
>
> 另：触发下载会中断导航，Playwright 同步 API 在这种情形下可能卡在 `close()`，
> 所以浏览器那步隔离在独立子进程里并设超时。

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

**#25 评估在 OSWorld-MCP 之上改造 vs 直接接 OSWorld** ✅ 已完成　依赖：#1–#6 基本完成
> **结论：在 OSWorld-MCP 之上改造。** 理由不是"省事"，而是它的接线方式恰好通用。
>
> 查阅 `mcp/osworld_mcp_client.py`（103 行）后确认：它用的是
> **`fastmcp.Client` + 标准 `mcpServers` 配置**，与 Claude Desktop / Claude Code
> 的配置同构，并且**已经同时接了 stdio 型的第三方 server**
> （`@modelcontextprotocol/server-filesystem`、`mcp-server-git`）——
> 接入外部 MCP 是它的既定用法，不是我们要去改造的地方。
>
> **实测验证**（不只是读代码）：用它同款客户端栈直连我们的二进制，
> `list_tools()` 返回 10 个工具，`call_tool("get_app_state")` 正常返回
> 687 token 的裁剪后树。**我们这边零代码改动。**
>
> 相比直接接 OSWorld 的优势：OSWorld 官方动作空间只有 pyautogui / computer_13
> 两种坐标动作，没有 MCP 接入点，从零接等于自己实现一遍 agent 循环与工具注入；
> 而 OSWorld-MCP 已经把这套做完并发表（ICLR 2026）。
>
> 两个要注意的：
> 1. OSWorld-MCP 最后提交于 2026-05-13，落后 OSWorld 主线约两个半月，合并时要处理版本差
> 2. **已决定：替换，不并存。** 只挂本 MCP，不启用它自带的 158 个 per-app 语义工具。
>    见决策记录 2026-07-30。

**#27 工具命名在误导模型：`perform_secondary_action` 听起来像兜底**　依赖：无
> 来自实际使用观察：**工具名本身在影响模型的选择**，而 `perform_secondary_action`
> 的字面意思是"次要动作"，读起来像 fallback。但事实相反——AT-SPI 的语义动作
> **就是 first-class 的路径**。Nautilus 里文件的 `menu` 动作就是调出右键菜单的
> 唯一正规方式，一点都不"次要"。
>
> **不是 Linux 独有**：macOS 侧同名，描述更弱——
> "Invoke a secondary accessibility action exposed by an element."，
> 树里还渲染成 `Secondary Actions:`（Linux 已改成 `More actions:`）。
> 名字继承自官方 Codex Computer Use schema，两个平台都对齐了它。
>
> **约束**：仓库有明确的工具面对齐目标
> （见 `docs/exec-plans/active/20260422-remaining-tool-official-alignment.md`），
> 单方面改名会破坏与官方 schema 及 macOS/Windows 的一致性。所以这不是
> "改个名字"那么简单，是要先决定对齐还是分歧。
>
> 三个选项，代价递增：
>
> | 选项 | 做法 | 代价 |
> |---|---|---|
> | A | 不改名，强化描述 + `serverInstructions` + 树里措辞 | 无对齐损失；但**名字仍是模型最强的模式匹配依据**，效果有限 |
> | B | 保留原名，另加一个语义清晰的别名工具（如 `invoke_element_action`），二者等价 | 多一个工具；模型大概率优先选名字更清楚的那个 |
> | C | 直接改名，接受与官方 schema 分歧 | 对齐破裂，跨平台一致性受损 |
>
> 我倾向 **B**：它是唯一能真正解决"名字即引导"的方案，而代价只是多一个工具——
> 而本计划已经为 `get_screenshot` 开过一个平台特有工具的先例。
> 但这条动的是对外协议面，该由人拍板。
>
> 相关：这与 #6（引导 agent 走 a11y 通道）是同一个问题的两面。#6 做的是
> 描述和 Note 层面的引导，而命名是比描述更强的信号。

**#28 截图会破坏瞬态弹层——双轨道观测的边界**　依赖：无
- 实测：GTK 右键菜单持有指针/键盘 grab，截图工具（`import` 等）向 X 抓取时
  菜单**立即关闭**。排查 Nautilus 右键菜单时踩到：截完图菜单就没了，
  一度误判成"右键无效"。
- 这对双轨道设计有直接影响：**VLM 轨道观测瞬态弹层时会破坏它要观测的状态**
  （右键菜单、下拉列表、tooltip），而 a11y 读取不抓取、不干扰。
  这是 a11y 优先的一条独立论据，与 token 成本无关。
- **✅ 已查清（2026-07-30）：本项目的 `get_screenshot` 不受影响。**
  `capture_window_png()` 走 `Gdk.pixbuf_get_from_window(root, …)`，直接读根窗口
  像素，**不调用 `XGrabServer`**。实测：右键菜单打开 → `get_screenshot` →
  再取状态，窗口仍是那个弹出菜单，菜单没有被关掉。
  会关菜单的是 ImageMagick `import` / `scrot` 这类**主动抓取 X 服务器**的工具。
- 保留价值：这条仍然是 a11y 优先的一条独立论据——不是"我们的截图有 bug"，
  而是**通用的截图工具会破坏瞬态 UI**，任何依赖外部抓屏的 VLM 方案都会踩到。
  排查时我自己就被它骗过一次：截完图菜单没了，一度误判成"右键无效"。

**#29 a11y 轨道到底该不该带截图——用实验定，不用直觉定**　依赖：#2b、#26
- 现状：#4 拆分双轨时让 a11y 轨道**不返回 image block**，理由是省 token。
  但"省了 token 是否换来更低的成功率"从来没有量过——这个决定目前是靠推理成立的，
  不是靠数据成立的。**用户明确要求把它挂起，等其它优化做完再回来对比。**
- 做法：同一批任务、同一个 harness，只切换 a11y 轨道带不带截图，跑两组。
- 口径：成功率 / 平均步数 / 平均 token / a11y 通道使用率 四元组各出一份，
  再看**每成功一个任务的平均 token**——只看单次观测成本会得出错误结论：
  截图贵，但如果它让步数减半，总成本可能更低。
- **已有一个截图不可替代的实证（2026-07-30）**：VS Code 改完 settings.json 后
  弹出原生对话框「A setting has changed that requires a restart to take effect.」，
  与 VS Code 同进程、`_NET_WM_WINDOW_TYPE_DIALOG`、锁死整个应用，
  **AT-SPI 里完全不存在**。此时树看起来一切正常、每个动作都被正确拒绝，
  而 a11y 通道**无法回答"为什么"**——只有截图能。
  这类"a11y 是瞎的"场景应当在 A/B 里被单独计数，而不是混进平均值。
- 特别注意：不要只在容易的任务上比。真正该看的是那些 a11y 树信息不足的场景
  （GIMP 画布 17.6% 必须看图、匿名 spin button 消歧），
  带图的价值集中在这些任务上，平均值会把它稀释掉。
- 验收：给出两组四元组对比 + 明确结论（默认带 / 默认不带 / 按任务类型切换）

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
- 2026-07-30：**在 OSWorld-MCP 之上改造，但只挂本 MCP、替换掉它自带的工具**。
  先验证过它不是另一个 benchmark：它的任务集是 OSWorld 官方的**严格子集**
  （361 / 369，自造任务 0 个，缺的 8 个全在 `multi_apps`），安装方式是把它的文件
  整合进 OSWorld——跑的就是 OSWorld 的任务和 OSWorld 的验证器。
  用它是为了复用那层通用接线（agent 循环、工具注入、多环境并行），
  而不是替代 OSWorld；官方动作空间只有 pyautogui / computer_13 两种坐标动作，
  没有 MCP 接入点，从零接等于把那篇 ICLR 工作重做一遍。

  **工具面选择替换而非并存**：只挂 unify-cu MCP 的通用 a11y 接口。
  代价是结果不能与 OSWorld-MCP 论文里的数字直接对比（它测的是 per-app 语义工具）；
  收益是"a11y 通道使用率"这个指标干净——测的是**通用 a11y 接口本身够不够用**，
  而不是"agent 在两套工具之间怎么选"。两者混在一起，低使用率将无法归因。
  若后续要与其论文对比，再单独跑一次并存配置。

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
