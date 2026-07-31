# 「操作系统上的 Playwright」：待办清单与规范（2026-08-01）

> 配套读：`20260731-os-playwright-alignment.md`（对齐计划与分阶段设计）、
> `20260731-os-playwright-progress.md`（实现进展与真实 agent 验证）、
> `20260730-impress-manual-run-findings.md`（手工跑 Impress 的九条局限）。

这份文档解决一个具体问题：前两份记录的是**做过什么**，这份记录的是
**还要做什么、按什么规矩做**。清单会随进展修改，规范一节不轻易改。

---

## 一、当前状态

| 项 | 值 |
|---|---|
| 工具面 | **13 个**（新增 `find` / `verify`） |
| CI | **170** 项通过 |
| 脚本化基线 | **5/5**，步数 3.2，token 11618（文本 6498 + 视觉 5120） |
| 两个轴 | 执行轴 a11y 52%；观测轴 视觉 44% |
| 分支 | `fix/linux-silent-input-failures`，已推送 |

### 已对齐 Playwright 的部分

| Playwright 的做法 | 我们的对应物 | commit |
|---|---|---|
| aria snapshot 文法 | 带引号转义的元素行 + `{x,y,w,h}` | `4b1f383` |
| `ariaSnapshot.ts` 的 ref 算法 | `StableIndexer`，编号跨快照存活 | `832a9b8` |
| `Call log:` 错误模板 | `toolError` 的 `add()` / `step()` | `90b06c7` |
| strict mode 歧义报错 | 命中多个**不挑一个**，列候选让调用方收敛 | — |
| `--caps=vision` 能力开关 | `OPEN_COMPUTER_USE_CHANNELS` | `aa684e5` |
| `--output-mode file` | 同名开关，VS Code 15342→294 字符 | `781ac3e` |
| **locator（不 dump DOM 就能定位）** | **`find`**，Thunderbird 7153→656 字符 | `d66aa16` |
| **`expect()` 自动重试** | **`verify`**，实测轮询到 +2.5s 条件成立才 PASS | `d66aa16` |
| **locator 在动作时重新解析** | `current_geometry()`，实测纠正 400px 偏差 | `14de0e8` |
| **动作前等 stable** | `wait_until_stable()`，两次采样一致为准 | `eb8422a` |
| 无障碍名字含 labelled-by | 无名控件借名字，「位置和大小」13/13 | `f96451f` |
| `error-context.md` | 失败时落盘失败当时的树 + 截图 | `4782344` |
| trace viewer | `OPEN_COMPUTER_USE_TRACE_FILE`，每动作一行 JSONL | `f0ea881`+ |

---

## 二、待办清单

**清单里的六条已全部完成**（2026-08-01）。判据与实测结果留在下面，因为
"做完了"这句话本身没有信息量，能被别人复核的判据才有。

| | 条目 | 判据 | 实测结果 | commit |
|---|---|---|---|---|
| P0 | `LABELLED_BY` 探针 | 「位置和大小」四个无名 spin button 能否拿到 `Position Y` | **13/13 全部拿到**；净增益率 1.98%，1.037ms/节点 | `f96451f` |
| P0 | modal 遮挡感知 | 对话框打开后顶部出现提示，关掉后消失 | 三例验过（Question / Tip of the Day / Position and Size） | `751ef00` |
| P1 | stderr 不再丢 | 故意写一行，成功响应里能看到 | `PROBE-STDERR-LINE (x2)` | `4782344` |
| P1 | error-context 落盘 | 失败后磁盘上有失败当时的树与截图 | 1556 字节 `.md` + 23678 字节 `.png` | `4782344` |
| P2 | step trace | 跑完基线后有每动作一条的 JSONL，默认关闭 | 16 行 / 17905 字节，1119 字节每行 | `f0ea881`+ |
| P2 | 回显语义标识 | `click` 后的 Note 含 index+role+name | `Resolved element_index 4 to push button 'Save'.` | `f0ea881` |

三条从做的过程里长出来的结论，比条目本身更值得记：

**`MODAL` 位和 `ENABLED` 一样不可靠。** LibreOffice 7.3 的「Tip of the Day」
是 `role=dialog`、`ACTIVE`、`SHOWING`，**却不设 MODAL**，而它确确实实挡在应用
前面。所以模态提示分成两档：有 MODAL 位才敢说"应用会忽略其它窗口的输入"，
否则只说"树是对话框的，不是主窗口的"，并写明"this is not proof that the app
is blocked"。只认 MODAL 会漏掉真实阻塞；混为一谈则是替不设 MODAL 的对话框打
我们无权打的包票。

**借名字差点原样复发 `5543a52`。** record 里存的是借来的 `Position Y`，而
`node_name()` 在同一节点上返回空串——身份判定必然失配，且失配是静默的。
修法是提一个唯一的名字口径 `effective_name()`，凡是比对名字的地方都从这里取。

**测量仪器自己会给假阴性。** 模态提示上线后基线 vlc-preference 稳定失败，
报"首选项没打开"，而对话框其实已经打开了——`window_title()` 取的是固定行号
`lines[1]`，任何诊断 Note 出现都会打偏它。按规范 6 先用上一个 commit 的二进制
做了隔离，确认是本次触发，但根因在仪器。仪器给假阴性比没有仪器更糟：
它会让人去修一个并不存在的产品缺陷。

### 下一批（尚未开工，判据待定）

- 横向滚动的滚轮按钮（b6/b7）实测——现在走按键，因为没测过
- `verify` 支持多条断言组合（现在一次只能断言一件事）
- `find` 的结果按"离你要找的东西有多近"排序，而不是按树的顺序
- 命中测试准确率能不能提高（现在 LibreOffice 12/25，只能是 HINT）

## 三、规范（不轻易改）

这些不是风格偏好，每一条都是踩坑换来的。改之前先看看它当初防的是什么。

### 1. 不许骗 agent

工具的输出必须反映**真实状态**。宁可少给一个信号，也不能给一个假信号。

- 已经因此撤回过的：`ENABLED` 缺失不等于 disabled（Nautilus 文件图标根本
  不设 `ENABLED`/`SENSITIVE` 却完全可点，标成 disabled 会让 agent 跳过可用目标）。
- 已经因此修过的：合成坐标的 Note 报屏幕绝对坐标，而树/截图/`click_xy`
  全是窗口相对——差一个窗口原点（实测 89,49）。
- 推论：**如实标注一个缺陷不等于修好它**。`scroll` 曾长期在描述里写着
  「element_index 不参与定位」，那是诚实的，但不是可以停在那里的理由。

### 2. 没测过的路径不上线

不许以「应该能 work」的名义发布一条没有实测证据的链路。

- 正例：横向滚动仍走 `Left`/`Right` 键，因为滚轮的横向按钮（b6/b7）没测过，
  并且这一点写在了工具描述里。
- 反例（已修）：`ACTION_TARGET_STATES` 曾按语义猜了一组动作名，实测在六个
  应用里**零次命中**——纯粹的死代码。

### 3. 判据要有对照组

只测「做了 X 之后有变化」不够，必须同时测「不做 X 时没有变化」。

- 滚轮实测：对照组（什么都不做）文本区 0% 像素变化，实验组 23%。
- 像素判据本身也是这么长出来的：先猜阈值 → 量噪声底 → 最后改成
  「变化要在连续两张后置截图里都存在」才算数（滤掉闪烁的文本光标）。

### 4. 推断不是测量

从「A 变了」推出「B 也变了」不算证据。

- 踩过：验证过期几何时，我拿「收起 VS Code 侧栏后树变了」当作元素移动的
  证明——而那个按钮在顶部横幅里，收侧栏根本不动它。换成右对齐元素 +
  改窗口宽度才拿到真证据（{1355,27} → {955,27}，正好 -400）。

### 5. 别照抄在这里不成立的判据

Playwright 的做法要先问「这条在 Linux 桌面上成立吗」。

- `enabled`：不成立，见规范 1。
- 「receives events」命中测试：不能当门禁——实测命中率 gedit 11/11、
  Nautilus 19/25、LibreOffice 12/25，当门禁会拦掉一半 LibreOffice 的正常点击。
- `stable`：成立，已实现。

### 6. 归因要隔离

怀疑「是我改坏的」时，用改动前的二进制跑同一个用例。

- `nautilus-rename` 稳定失败时，先用 `git worktree` 建了 HEAD 的构建跑一遍，
  确认同样失败，才去查环境——最后查出是合成右键在这个 GTK4 版本上不通，
  是先前就存在的真缺陷，不是本次回归。

### 7. 一个应用的证据不能否掉另一些应用的唯一出路

- 合成右键在本机 Nautilus 上 100% 失效，但**保留**为最后兜底，因为它在
  别的工具包上是通的。新增的 `Shift+F10` 排在它前面，而不是替掉它。

### 8. 每次改动都要过 CI + 基线，且提交信息写清「为什么」

- CI 170 项、基线 5/5 是门禁。基线掉了先归因（见规范 6），不许直接接受。
- 提交信息里写下**踩过的坑和撤回过的结论**，那比写「做了什么」更有价值。

---

## 四、明确不做的（硬限制）

这些不是待办，是桌面本身的边界。写在这里是为了不再反复讨论。

| 限制 | 事实 |
|---|---|
| 并行 / 后台执行 | XTEST 是全局输入，Linux 上没有 per-process 定位。Playwright 的 browser context 隔离在桌面上没有对应物 |
| 命中测试当权威 | 只能是 HINT：gedit 11/11、Nautilus 19/25、LibreOffice 12/25。DOM 的 `elementFromPoint` 是权威的，AT-SPI 的不是 |
| 树永远存在 | 应用不给树就是不给：GIMP 画布零节点、`get_app_state` 30s 超时。浏览器里 DOM 永远在 |
| 变化有事件 | 桌面没有「页面加载完成」。`wait_for_ui_to_settle` 有实测过的边界：0.12s 之后才出现的窗口抓不到 |

---

## 五、未竟事项

并行深挖 Playwright 的 workflow（`wf_87535c83-26d`）**没有跑完**。17 个 agent
里 10 个的最后一行是 `[Request interrupted by user]`——每次对话被中断或 resume，
在飞的 agent 都跟着被杀；最后一次想 resume 时工具调用 ID 已失效。

上面这份清单是独立定位出来的，不依赖它的产出。若要重启这类调研，**别在
对话里跑长 workflow**：用 `/workflows` 看进度（不打断），或者干脆拆成几个
短的分批跑。
