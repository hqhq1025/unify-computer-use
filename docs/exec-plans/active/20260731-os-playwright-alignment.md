# 计划：把这套 MCP 做成「操作系统上的 Playwright」

> 起点：用户提出「我们想做 os 上的 playwright」，并指了 Playwright MCP 与
> WeaveBench 两个参照。本文先把两者的设计取准，再逐条对照出我们的差距，
> 最后给分阶段计划。
>
> 相关：`20260730-impress-manual-run-findings.md`（本文引用的实测都出自那轮）、
> `20260730-linux-a11y-first-osworld.md`（总 backlog）。

---

## 0. 我们要抄的到底是什么

Playwright 值钱的地方**不是"能驱动浏览器"**，而是它在**接口层面**把两件事定死了：

1. **默认通道是 a11y 快照**，动作靠快照里的引用定位，不靠像素。
   官方原话：*"Uses Playwright's accessibility tree, not pixel-based input"*。
2. **坐标是一个独立的、可关的能力**（`--caps=vision`），工具名自带 `_xy` 后缀
   （`browser_mouse_click_xy` / `browser_mouse_drag_xy`），
   **模型光看名字就知道自己在哪条通道上**。

而 WeaveBench 补上了第三条，也是最要紧的一条——**通道之间不可互相替代**：

| 通道 | 能拿到什么 | 它的原话 |
|---|---|---|
| GUI | 瞬时渲染态（canvas、对话框、图表） | "no API returns" |
| CLI/code | 持久态（配置、日志、服务） | "no screenshot can produce" |

它在评分里硬性执行这条：证据要打 `STRUCT:` / `VISUAL:` 标签，
**只用单通道的轨迹硬封顶 0.4 分**。

> 我们要做的就是这件事的 OS 版：**给通道起名字、把名字写进接口、
> 并且说清它们不是替代关系。**

---

## 1. 逐条对照

| 维度 | Playwright | 我们（现状） | 差距性质 |
|---|---|---|---|
| 快照语法 | `- role "name" [attr=value]`，YAML 缩进 | `<idx> role name [states] Value: … Description: … Frame: {…}`，TAB 缩进 | **可解析性缺陷**，见 2.1 |
| 名字定界 | 双引号 | 裸的 | **真 bug**，见 2.1 |
| 值 | 冒号后：`- textbox: Enter your name` | `Value: xxx` 字段 | 字段名会和名字内容撞车 |
| 状态 | `[checked]` `[level=1]` `[expanded]` | `[focused]` `[has-click-action]` | 语法已经一致，可直接对齐取值 |
| 元素引用 | `target`：快照 ref **或** selector | 数字 `element_index`，仅 ref | 缺 selector，跨快照必失效 |
| 意图声明 | 每个动作都有 `element`：*"Human-readable element description"* | **没有** | **真缺陷**，见 2.3 |
| 几何 | **默认不给**，`boxes` 开关才有 `[box=x,y,w,h]` | **每行恒带** `Frame: {…}` | **占 35–50% 的树**，见 2.2 |
| 坐标动作 | 独立能力 `--caps=vision`，名字带 `_xy` | 混在 `click` 里，`drag` 名字看不出通道 | 本轮 P0 修 |
| 截图 | 明说"不能基于截图做动作" | 现在恒带，且 `drag` 不可关 | **我们与它的真实分歧**，见第 4 节 |

---

## 2. 对照暴露出的三个真缺陷

### 2.1 名字没有定界符 → 这棵树无法可靠解析

实测 Impress 的一行：

```
26 paragraph  Value: Weekday in school Description: Paragraph: 0 Weekday in school Frame: {…}
```

**名字本身可以含冒号**——同一棵树里就有 `panel PageShape: Weekday in school`。
于是 `Description:` 这个分隔符和名字内容在词法上无法区分：agent 想从一行里
切出"名字到底是什么"，没有任何可靠办法。

Playwright 用引号定界正是为了这个。这不是审美问题，是**我们发出去的是一种
歧义文法**。

### 2.2 几何恒带，占掉树的 35–50%

实测（`OPEN_COMPUTER_USE_A11Y_SCREENSHOTS=0`，只算 `treeLines`）：

| 应用 | 行数 | 总字符 | `Frame:` 字符 | 几何占比 |
|---|---|---|---|---|
| LibreOffice | 189 | 17694 | 8899 | **50%** |
| gedit | 14 | 1259 | 601 | **47%** |
| Nautilus | 85 | 8140 | 3930 | **48%** |
| VS Code | 191 | 24731 | 8834 | **35%** |
| Thunderbird | 119 | 12551 | 5552 | **44%** |

**每两个 token 里就有将近一个是坐标。** 而 a11y 通道的动作**根本不用坐标**
——它们用 `element_index`。坐标只有 GUI 通道要，而 GUI 通道现在恒带截图，
坐标本来就该从图上来。

Playwright 把几何做成 `boxes` 开关，是同一个判断。

### 2.3 没有"意图声明"，工具接不住"下标解析到了别的控件"

Playwright 每个动作都带一个 `element` 参数：
*"Human-readable element description used to obtain permission to interact with the element"*。
它**不用于定位**，用于可读性与权限——但顺带产生了一个很强的副作用：
**工具可以拿 agent 声明的意图去交叉校验解析结果**。

我们只有一个数字。`record_still_matches()` 能拿"上次存下来的记录"比对，
但比不了"agent **想**点的是什么"。

实测后果（我自己踩的）：F4 打开对话框后索引全变，我用上一份快照的下标
调 `click(element_index=5)`，工具照点不误——本想点 Position Y，实际点到菜单，
**把对象高度误改成了 16.26cm**，而且全程没有一条报错。

有了意图声明，这一步会变成：

```
拒绝：element_index=5 解析到 `menu Insert`，
      与你声明的 "Position Y spin button" 不符。请重新 get_app_state。
```

---

## 3. 分阶段计划

阶段之间**只有 P0 是已决策的**，其余需要逐个确认再动——它们都会改变
agent 看到的东西，改完都要重跑基线。

### P0 — 通道按名字分家 ✅ 已决策

- `click` **去掉 `x`/`y`**，只留 `element_index`（accessibility 通道）
- 新增 `click_xy(app, x, y, click_count, mouse_button)`（GUI 通道）
- `drag` → **`drag_xy`**（本来就只有坐标）
- Note 标签变成**两个轴各一个**：`[a11y][semantic]` / `[gui][synthesis]`
  （保留执行轴的原标签，基线脚本的执行轴指标不受影响）
- 每个工具的 description 开头加一行 `Channel: …`
- `serverInstructions` 里把两条通道讲成**不可替代**，而不是主备

风险：与官方 Codex schema 分家更多。`#27` 已有先例（用户拍板方案 C）。

### P1 — 快照语法向 aria snapshot 靠

目标行形态：

```
- 26 paragraph "…" [focused]: Weekday in school
- 25 panel "PlaceHolder 1"
- 12 button "OK" [has-click-action]
```

- 名字加**双引号**（修 2.1）
- 值移到**冒号后**，去掉 `Value:` 字段名
- 状态维持 `[k]` / `[k=v]`
- **保留数字下标**：我们没有 selector，下标是唯一的引用手段（P4 再解决）

风险：这是 agent 直接读的格式，token 数与可读性都会变，**必须重跑基线**。

### P2 — 动作加 `element` 意图声明（修 2.3）

- 所有吃 `element_index` 的工具加一个 `element` 参数（先可选，观察一轮后转必填）
- 服务端**交叉校验**：解析出的 role/name 与声明明显不符就拒绝，
  并把两边都打印出来
- 这条**独立于 P1/P3**，可以先做——它接住的是一类静默错误，收益最直接

### P3 — 几何改成 opt-in（修 2.2）

- `get_app_state` 加 `boxes`（默认 `false`），只有它为真才渲染 `Frame:`
- GUI 通道要坐标就从**截图**取——这正是通道分家的意义
- 预期省掉 35–50% 的树 token

风险：现有依赖 `Frame:` 的用法（包括本仓库的脚本）要一起改。
**做之前先确认 `click_xy`/`drag_xy` 的坐标确实能从截图取到**——
本轮我那次成功的拖拽，坐标是从树里算的，不是从图上读的。
**这一条没验证之前不要动 P3。**

### P4 — 稳定 selector

Playwright 的 `target` 可以是 ref **或 selector**。我们可以支持
`role:name` 之类的稳定选择器，让引用跨快照存活，从根上解决"下标错位"。

### P5 — 能力开关

像 `--caps=vision` 那样，让 GUI 通道整体可关。
`OPEN_COMPUTER_USE_A11Y_SCREENSHOTS` 已经是雏形，
`#29` 的 A/B 需要的正是这个。

---

## 4. 我们和 Playwright 的**真实分歧**——别盲目对齐

Playwright 敢把坐标做成边缘的 opt-in 能力，是因为**在浏览器里，一切都可 ref 定位**。
DOM 是完备的。

**OS 上这个前提不成立。** 已实测三例 a11y **结构性看不见**：

1. **GIMP 画布**——整棵树里 `canvas` 角色节点数为 **0**
2. **VS Code 原生文件对话框**——与编辑器同进程，但不在 AT-SPI 树里
3. **VS Code 设置改动后的重启提示**——同上，且吞掉所有按键

外加本轮新增的两条：

4. **`drag` 的效果不进树**——把 Impress 标题从 0.76cm 拖到 15.00cm，
   元素的 `Frame` 一点没变
5. **格式类改动不进树**——右对齐与保存都生效了，树却字节不变，
   于是被判成"送达但被忽略"（两次假阴性）

所以：

> **GUI 通道在我们这里必须是一等公民，不能像 Playwright 那样做成边缘能力。**
> 我们抄它的**通道分层与命名纪律**，不抄它的**通道权重**。

这也是 WeaveBench 那条"通道不可互相替代"在 OS 上的具体含义：
Playwright 可以说"你不能基于截图做动作"，我们不能。

---

## 5. 建议的执行顺序与理由

| 顺序 | 阶段 | 理由 |
|---|---|---|
| 1 | **P0** 通道分家 | 已决策；纯接口层，不改语义，风险最低 |
| 2 | **P2** 意图声明 | 独立、收益最直接——它接住的是一类**静默**错误 |
| 3 | **P1** 语法对齐 | 修可解析性缺陷；要重跑基线 |
| 4 | **P3** 几何 opt-in | 省 35–50% token，但**必须先验证坐标能从截图取** |
| 5 | **P4/P5** | 长线 |

**P2 排在 P1 前面**是刻意的：语法再好看，接不住"点错了对象"也没用；
而静默操作错误对象是本项目已确认的、最贵的一类失败。

---

## 6. 尚未确认的事

- **"ale" 是什么**：用户提到的第三个参照没找到对应项目，
  搜到的只有 Playwright MCP 与 WeaveBench。需要用户给个链接。
- **P3 的前置**：坐标到底能不能可靠地从截图读出来。
  本轮那次成功的拖拽坐标来自 a11y 树，不是截图——所以
  "GUI 通道自给自足"这件事**尚未验证**。这是 P3 的硬前置。
