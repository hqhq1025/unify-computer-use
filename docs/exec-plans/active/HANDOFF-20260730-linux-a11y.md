# 交接：Linux a11y-first / OSWorld（截至 2026-07-30 20:40）

> 新会话请**先读这一份**，再按需翻
> `docs/exec-plans/active/20260730-linux-a11y-first-osworld.md`（完整 backlog）。

## 一句话状态

七个应用里**六个**的高频操作已走通并全部以外部真值验收；`#3` / `#26` / `#27` /
`#30` 完成，`#29` 完成成本侧。仓库干净、CI 全绿。剩下的都有明确起点。

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
成功率 5/5 = 100%   平均步数 3.2   平均 token 10157   a11y 通道占比 52%
```

| 任务 | 工具包 | 步数 | token | a11y |
|---|---|---|---|---|
| thunderbird-folder | Gecko | 1 | 6264 | 100% |
| vscode-edit | Electron | 4 | 22679 | **0%** |
| nautilus-rename | GTK | 5 | 8287 | 66% |
| vlc-preference | Qt | 3 | 12282 | 100% |
| gedit-type | GTK | 3 | 1271 | 33% |

> 加入 Electron 前是 69% / 7026 token。**把最不可靠的工具包排除在外，
> 指标会显著好看于实际**——这个基线是 A/B 的基准，抽样偏了结论就全错。

---

## 二、接下来的 TODO（按建议顺序）

### 1. 修 `main_window()` 多窗口选错 —— **优先做，是个真 bug**

- **现象**：`Message Filters` 对话框开着时，`get_app_state` 返回的却是主窗口
  `Inbox - Local IMAP`。按索引点 `New…`，实际点到主窗口的「新建邮件」，
  弹出 `Write: (no subject)`。
- **性质**：**静默操作错误对象**——与陈旧 `element_index` 同源，但索引是新取的、
  没过期，错的是**取状态时就选错了窗口**。
- **成因**：`main_window()` 顺序是 `模态 > ACTIVE > SHOWING > 第一个`，
  而 `Message Filters` 既不报 MODAL 也不是 ACTIVE，输给了主窗口。
  今天为 LibreOffice 模态框加的优先级修复只覆盖**上报 MODAL** 的那类，这里是盲区。
- **可能方向**：SHOWING 候选里让 `dialog`/`alert` 角色优先于 `frame`；
  或把"最近出现的顶层窗口"纳入判据；或给动作工具一个"指定窗口"的入口。
- **注意**：这条动的是**每个应用都走的核心路径**，务必先复现再改，改完跑
  `scripts/measure-baseline.py` 全量确认没有回归。

### 2. Thunderbird 消息过滤器（3 个任务）

- 环境已就绪：本地 dovecot IMAP + 一次性凭据 `mcptest` / `mcptest123`
  （见下文"环境"一节），**不要用用户的真实密码**。
- 现状：`AppMenu → Tools → Message Filters` 能开，`Run Now/New…/Edit…/Delete`
  可寻址；但点 `New…` 纯语义 0 次合成、无子窗口。
- **起点**：先在 `combo box Filters for:` 里选中 `Local IMAP` 账户再点 `New…`。
  注意这一步很可能会撞上上面第 1 条的窗口选错问题——建议先修 1 再做 2。

### 3. `#29` 收益侧（截图 A/B）

- **不能用 `measure-baseline.py` 做**：脚本化任务链不做决策，带不带截图都走同样
  步骤，成功率与步数必然不变、只剩 token 差，会得出"截图纯属浪费"的错误结论。
- 需要一个**会做判断的 LLM arm**。
- 重点看**三例 a11y 结构性看不见**的场景（见下），它们应**单独计数**而非混入平均值。

### 4. `#2c` L3 穷尽面板

- 用户定的规则：**L2 全闭合之后才开始**。目前 L2 还差消息过滤器 3 个任务。

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
- Thunderbird profile：`~/.thunderbird/wtkk3c2w.default-release`，
  已配 `Local IMAP` 账户（`server2`），**该目录由 Thunderbird 启动时创建**
- OSWorld 官方仓库浅克隆在 `/home/user/OSWorld`（370 个任务文件）
- 驱动脚本 `/tmp/drive.py`（单会话 MCP 客户端，动作类工具必须先 `get_app_state`）

---

## 五、已证伪的路径（别重走）

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

**验收一律用外部真值**：文件系统、保存后的 XML、应用配置文件、画布像素、
窗口标题——**不采信工具自己的 `isError`**，也不采信被操作控件自身的状态
（VLC 那颗单选按钮 `CHECKED` 翻转了但面板没切换）。
