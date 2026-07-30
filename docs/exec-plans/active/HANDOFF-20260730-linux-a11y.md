# 交接：Linux a11y-first / OSWorld（截至 2026-07-30 21:30）

> 新会话请**先读这一份**，再按需翻
> `docs/exec-plans/active/20260730-linux-a11y-first-osworld.md`（完整 backlog）。

## 一句话状态

七个应用里**六个**的高频操作已走通并全部以外部真值验收；`#3` / `#26` / `#27` /
`#30` 完成，`#29` 完成成本侧。仓库干净、CI 全绿（124 测试）。

> **上一版交接把「`main_window()` 多窗口选错」列为第一优先，那是误判，已更正。**
> 详见第五节。现在的第一优先是 Thunderbird 消息过滤器。

---

## 一、这次做完了什么

| 待办 | 结果 |
|---|---|
| `#3` 效果判据 | ✅ click 自动回落 + 纯合成工具区分"送达/生效" |
| `#26` 四元组基线 | ✅ `scripts/measure-baseline.py`，5 任务 / 4 工具包 |
| `#27` 工具改名 | ✅ `perform_secondary_action` → `invoke_element_action`（方案 C） |
| `#30` 崩溃恢复 | ✅ `scripts/verify-libreoffice-crash-recovery.py` 端到端通过 |
| `#29` 截图 A/B | 🔶 成本侧已实测；收益侧需 LLM arm |

**L2 逐应用（`#2b`）**：Nautilus ✅ / Writer ✅ / Calc ✅ / VS Code ✅ /
VLC ✅ / GIMP ✅ / Thunderbird 🔶（只剩消息过滤器 3 个任务）

### 四元组基线（当前值，#29 的基准）

```
成功率 5/5 = 100%   平均步数 3.2   平均 token 10227   a11y 通道占比 52%
```

| 任务 | 工具包 | 步数 | token | a11y |
|---|---|---|---|---|
| thunderbird-folder | Gecko | 1 | 6618 | 100% |
| vscode-edit | Electron | 4 | 22679 | **0%** |
| nautilus-rename | GTK | 5 | 8287 | 66% |
| vlc-preference | Qt | 3 | 12282 | 100% |
| gedit-type | GTK | 3 | 1271 | 33% |

> 加入 Electron 前是 69% / 7026 token。**把最不可靠的工具包排除在外，
> 指标会显著好看于实际**——这个基线是 A/B 的基准，抽样偏了结论就全错。

---

## 二、接下来的 TODO（按建议顺序）

### 1. Thunderbird 消息过滤器（3 个任务）—— **现在是第一优先**

- 环境已就绪：本地 dovecot IMAP + 一次性凭据 `mcptest` / `mcptest123`
  （见下文"环境"一节），**不要用用户的真实密码**。密码已存进 Thunderbird
  的密码管理器，正常启动不再弹框。
- **卡点已定位到具体一步**：`AppMenu → Tools → Message Filters` 能开，
  `get_app_state` 稳定照到 `Message Filters` 窗口，`New…` 也能正确寻址
  （实测 `[13] role=push button name='New…'`）——但**点下去什么都没发生**：
  纯语义 0 次合成，`wmctrl` 看不到任何新窗口。
- **起点**：先在 `combo box Filters for:` 里选中 `Local IMAP` 账户再点 `New…`。
  若仍无反应，这就是第 5 种失败（语义动作被接受但不生效）的又一例，
  该走 `click_method: "global"` 用元素锚定坐标点。
- 复现脚本：`/tmp/repro_filters.py`（会打印照到哪个窗口、点了什么、多了什么窗口）。

### 2. `#29` 收益侧（截图 A/B）

- **不能用 `measure-baseline.py` 做**：脚本化任务链不做决策，带不带截图都走同样
  步骤，成功率与步数必然不变、只剩 token 差，会得出"截图纯属浪费"的错误结论。
- 需要一个**会做判断的 LLM arm**。
- 重点看**三例 a11y 结构性看不见**的场景（见下），它们应**单独计数**而非混入平均值。

### 3. `#2c` L3 穷尽面板

- 用户定的规则：**L2 全闭合之后才开始**。目前 L2 还差消息过滤器 3 个任务。

### 4. 可选：`resolve_app` 的 0.15~0.20s

- 实测每次动作后 `resolve_app` 要 0.15~0.20s（遍历整个桌面 + 重试逻辑）。
- 它目前**顺带**挡住了一个 53ms 的时序危险窗口；`4f64b35` 已把那道防线换成
  显式的状态判据，所以现在优化它是安全的。**改之前先确认 `4f64b35` 还在。**


---

## 三、下个会话必须知道的结论

### 执行失败有五类，处置各不相同

| 类型 | 实例 | 处置 |
|---|---|---|
| 什么都没发生 | GIMP 图层 `activate` | `auto` 自动回落坐标 |
| 做了别的事 | LibreOffice 下拉提交 | **调用前**避开语义通道 |
| 状态变了行为没变 | VLC Simple/All 单选 | **通用判据接不住**；用元素锚定 `global` |
| 点击导致焦点丢失 | Monaco 编辑器 | 别点，全程键盘；丢了用 `ctrl+1` |
| 焦点被树外的东西拿走 | VS Code 原生对话框 | 诊断会报出焦点持有者，引导截图 |

### 三例「a11y 结构性看不见」（换任何环境都测不了）

1. **GIMP 画布**——整棵树里 `canvas` 角色节点数为 **0**
2. **VS Code 原生文件对话框**——与编辑器同进程，但不在 AT-SPI 树里
3. **VS Code 设置改动后的重启提示**——同上，且会吞掉所有按键

> 与「环境阻塞」（如 Thunderbird 缺账户）**必须分开统计**：前者换环境也测不了，
> 后者换个环境就能测。混在一起会同时高估环境问题、低估 a11y 的结构盲区。

### 工具包的语义执行可靠性排序

**Qt > Gecko ≈ GAIL > GTK > Electron**

- Qt 最好：菜单项**不必展开菜单**就能语义调用
- Electron 最差：默认动作名不标准（`doDefault`）、点击会丢焦点、原生对话框隐形

### 两个被实测推翻的既有结论

- **a11y 树并非普遍更便宜**：gedit 树 349 vs 截图 1014（截图贵 2.9x），
  但文件管理器树 2135 vs 截图 756（**截图便宜 0.4x**）。
  → a11y 优先真正的支撑点是**可操作性**（给得出 `element_index`），不是省 token。
- **Monaco 并非只能纯键盘**：整份文档内容完整暴露在 `entry` 的 Value 里，
  **读是语义可行的**，只有写要回落键盘。

---

## 四、环境（本机现状）

- **dovecot 已装并在跑**（`/usr/sbin/dovecot`，注意 `which dovecot` 查不到，
  普通用户 PATH 里没有 `/usr/sbin`——我为此绕了很大一圈）
- **一次性 IMAP 凭据**：`mcptest` / `mcptest123`
  （`/etc/dovecot/users` + `/etc/dovecot/conf.d/99-mcptest.conf`）
  **不要使用用户的真实系统密码**——把它内联进脚本会被权限策略拦下，也不该做
- 测试邮件在 `/var/mail/user`（两封）
- **Thunderbird 已勾选「记住密码」**，正常启动不再弹密码框。若哪天又弹了，
  它是 MODAL 窗口、会挡住整棵文件夹树，`thunderbird-folder` 基线会直接失败
  （报「文件夹树里既没有 Inbox 也没有 Trash」）——先处理它再看别的
- Thunderbird profile：`~/.thunderbird/wtkk3c2w.default-release`，
  已配 `Local IMAP` 账户（`server2`），**该目录由 Thunderbird 启动时创建**
- OSWorld 官方仓库浅克隆在 `/home/user/OSWorld`（370 个任务文件）
- 驱动脚本 `/tmp/drive.py`（单会话 MCP 客户端，动作类工具必须先 `get_app_state`）

---

## 五、已证伪的路径（别重走）

- **`main_window()` 多窗口选错**——`d88fd93` 报过、后来**证明是误判**（详见 plan
  里的更正条目）。Gecko 把 `Message Filters` 报成 `frame` 且**确实上报 ACTIVE**，
  `get_app_state` 稳定照到它，端到端 8/8。别再去改 `main_window()` 的判据顺序。
- **拿 X11 `_NET_ACTIVE_WINDOW` 当 `main_window()` 的补充判据**——实现过、
  量过、又撤了。匹配本身很干净（`get_origin()`+`get_geometry()` 对上 AT-SPI 的
  SCREEN extents，GTK/Qt 精确相等，VCL 要改比含窗饰的 `get_frame_extents`），
  但**它与 AT-SPI 的 ACTIVE 在同一时刻翻转**（实测都是 0.1233s），
  对时序问题不提供任何新信息，其余场景又找不到一个能改变答案的已验证案例。
  给每个应用都走的核心路径加一条没有验证案例的判据，不划算。
  补丁留在 `/tmp/x11-focus-tier.patch`（会随机器重启消失，需要就照上面重写）。
- **手工造 mbox 让 Thunderbird 认**：裸 mbox、加 `X-Mozilla-Status` 头、
  语义点击 Inbox、坐标点击 Inbox、删 `folderCache.json` ——五种做法全部失败
- **`movemail` 账户类型**：Thunderbird 115 已移除（91 之后），写进 prefs.js 会被
  启动时重写掉
- **用户态 IMAP 服务器**：PyPI 上没有现成可用的服务端


---

## 六、方法论（这次最该保留的东西）

**工具不许替 agent 打包票，我也不许替自己打包票。**

这一轮至少七次差点固化未经验证的说法，六次自己抓回来、一次被推着做才发现：

| 差点写死的 | 实际 |
|---|---|
| `[disabled]` 标记 | Nautilus 文件图标不设 ENABLED 却完全可操作 |
| `[clickable]` 命名 | 它保证"有动作"，不是"点得动"→ 改 `[has-click-action]` |
| instructions `will work` / `cheap` | 前者会撒谎，后者在文件管理器上是反的 |
| LibreOffice `Yes` 撒谎 | 是我叠了 5 个弹窗造成的 |
| 名称框跳转 | 未验证就写进了给 agent 的提示 |
| Thunderbird 需要真实账户 | 局部验证当全称结论，还贴了"已实测"标签 |
| dovecot 未安装 | `which` 查不到 ≠ 没装 |
| `main_window()` 多窗口选错 | **看到坏结果就猜成因**，猜错了类别；实测三条依据全不成立 |

**验收一律用外部真值**：文件系统、保存后的 XML、应用配置文件、画布像素、
窗口标题——**不采信工具自己的 `isError`**，也不采信被操作控件自身的状态
（VLC 那颗单选按钮 `CHECKED` 翻转了但面板没切换）。
