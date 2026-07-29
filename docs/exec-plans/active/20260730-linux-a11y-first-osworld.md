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
- `list_apps` / `get_app_state` 不返回已退出应用的僵尸条目

**S2 观测成本**
- 默认观测**不含截图**；截图只在明确需要时出现
- 上表中位数应用的单次观测 ≤ **1.5k token**（当前中位约 3.2k 文本 + 1k 截图，最差 21.3k + 截图）
- 裁剪后**不丢失任务关键元素**（度量方法见 P1）

**S3 端到端能力**
- 先在 OSWorld 子集上量出 baseline，再据此设定成功率目标
- 报告口径固定为三元组：**成功率 / 平均步数 / 平均 token**，不单独报成功率

## 推进顺序

**先把 Linux 链路的缺陷排查干净、测试做扎实，再接 OSWorld。**

理由：baseline-first 这条原则适用于**优化**（裁剪、呈现改造这类"改了不知道有没有变好"的工作），
不适用于**缺陷修复**——"报成功但什么都没发生"是自明的错误，不需要基线来证明。
反过来，在一个已知存在静默失败的 runtime 上跑 OSWorld，量到的是 bug 而不是能力，
那个基线本身是噪音，后续所有对比都建立在流沙上。

所以 OSWorld 接入被排到最后；可靠性和双轨拆分不依赖它，立即开始。

## 里程碑

### P0 · 可靠性攻坚（无前置，立即开始）
- 僵尸 AT-SPI 注册：应用退出后 `list_apps` 仍列出、`get_app_state` 返回 2 节点空壳。
  `resolve_app` / `list_apps` 需校验进程存活或 frame extents 有效
- `click` / `press_key` 的效果仍无法区分"生效"与"送达但无响应"，需补充判据
- 系统性排查：对基线表里的 9 个可用应用逐个走完整动作集
  （`click` / `type_text` / `press_key` / `scroll` / `drag` / `set_value` / `perform_secondary_action`），
  每个动作用 AT-SPI 真值做 before/after 判定，把失败面完整列出来再逐条修
- 每修一条，同步补回归测试（`runtime_test.py` 单测 + `verify-linux-input-chain.py` 端到端）

### P0 并行 · 观测双轨拆分（可后台推进，与上面互不阻塞）
- 把 a11y 与 VLM 拆成两条显式 track，**a11y 为默认，VLM 为兜底**
- **a11y track 不带截图**。只有 a11y 确实做不到某个功能时，才切到 VLM track
- `build_snapshot()` 不再无条件截图；动作工具的返回默认不带图（当前带）
- 需要确定的接口形态：是加参数、加独立 tool，还是由 server 依据信号自动决定
- 切轨信号可直接复用现有标注：`Delivery ... was not verified`、`Nothing observable changed`、
  以及 a11y 树为空/过小

### P0 并行 · 对齐 macOS 参考实现
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

### P1 · 裁剪与呈现（先移植，再研究）
- **先做对照移植**：上表里 `shouldSkipChild` / `isPlainGenericTextContainer` /
  `placeholderValue` / `isSiblingCounterText` 直接对应裁剪需求，macOS 已有验证过的判据
- **再做研究**：macOS 没有回答的部分才需要实验（增量观测、扁平列表 vs 缩进树、查询式接口）
- 无论移植还是研究，都要先过保留率 / 压缩率离线评测

### P2 · 环境就绪度工具化
- a11y readiness probe 收进仓库，作为环境自检入口
- 沉淀解锁配置清单（当前已知：Chrome/Electron 需 `--force-renderer-accessibility`
  且必须独立 `user-data-dir`，否则参数被现有会话交接吞掉）

### P3 · OSWorld 接入与基线
- harness 跑通，能加载任务、执行、调用验证器
- 选定首批任务子集（建议从 LibreOffice 起步：任务密度高、a11y 完整、已有修复积累）
- 产出 baseline 三元组
- harness 观测策略：默认只给 a11y 树，VLM 事件驱动触发而非固定步数

## P1 研究议题：a11y 该怎么给 agent

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
| H1 | 只保留屏幕可见节点，不丢任务关键元素 | GTK 系 ~88% 压缩 | 未展开菜单项被裁掉后，agent 不知道功能存在 |
| H2 | 扁平索引列表比缩进树更省 token | 去掉缩进与重复层级 | 丢失层级语义，可能影响定位判断 |
| H3 | 过滤纯结构性容器（filler/panel/separator） | 中等 | 少数容器本身可点 |
| H4 | 增量观测：只给相对上次的变化 | 多步任务收益最大 | 需要稳定的元素标识；agent 需能请求全量 |
| H5 | 菜单等层级按需展开，不预先枚举 | 直击 LibreOffice 的虚高 | 多一轮交互 |

H1 有最强的数据支持，建议先做。H4 潜在收益最大，但依赖 P0 的元素标识稳定性，排在后面。

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

## 风险

- 风险：裁剪丢失任务关键元素，成功率下降而不自知
  - 缓解：P1 先建保留率评测，任何裁剪方案先过离线评测再上线
- 风险：抢焦点打断共享桌面的使用者
  - 缓解：OSWorld 场景 agent 独占桌面，代价可接受；已记入 `docs/ARCHITECTURE.md`
- 风险：语义动作空间与 OSWorld 官方 pyautogui 动作空间不同，分数不可比
  - 缓解：接受不可比，报告口径固定为三元组并注明设定（见决策记录）
- 风险：snap 打包的应用无法接入 a11y
  - 缓解：环境层面改用 deb/flatpak，或该应用降级走 VLM。运行时无解
- 风险：在缺陷未清理干净的 runtime 上接 OSWorld，量到的是 bug 不是能力
  - 缓解：P0 先做完并有回归防护，OSWorld 排到 P3

## 验证方式

- 命令：
  - `(cd apps/OpenComputerUseLinux && go test ./... && python3 -m unittest runtime_test)`
  - `scripts/verify-linux-input-chain.py --app <app>`
  - `scripts/a11y-readiness-probe.py`（应用 a11y 就绪度与观测成本）
- 手工检查：
  - 首批 OSWorld 子集的逐任务轨迹回放
- 观测检查：
  - 每次改动同时报告成功率 / 平均步数 / 平均 token，缺一不可

## 进度记录

- [x] 修复 Linux 输入链路两处静默失败（选错可编辑控件、全局合成误投），并补齐回归防护
- [x] 完成 10 个应用的 a11y 就绪度实测，产出基线表与观测成本构成
- [x] `scripts/a11y-readiness-probe.py` 收进仓库
- [ ] P0：僵尸 AT-SPI 注册修复
- [ ] P0：9 个应用 × 7 个动作的系统性排查，产出失败面清单
- [ ] P0：`click` / `press_key` 的效果判据
- [ ] P0 并行：观测双轨拆分，a11y track 不带截图
- [ ] P0 并行：对照 macOS 实现逐条补齐能力缺口（见缺口表）
- [ ] P1：建立保留率 / 压缩率离线评测
- [ ] P1：移植 shouldSkipChild / isPlainGenericTextContainer / placeholderValue
- [ ] P1：验证 H1（可见性过滤）
- [ ] P3：OSWorld harness 跑通并产出 baseline 三元组

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
- 2026-07-30：**观测双轨拆分的优先级高于树裁剪**。
  实测发现 `build_snapshot()` 无条件截图，`get_app_state` 和所有动作工具的返回都带图，
  gedit 单次观测里截图占 35%。裁剪把文本砍掉 ~88% 之后，截图会升到约 80%，
  成为新的主要成本——先拆轨，裁剪的收益才兑现得出来。
