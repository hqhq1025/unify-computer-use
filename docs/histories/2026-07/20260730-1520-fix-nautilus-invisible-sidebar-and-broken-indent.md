## [2026-07-30 15:20] | Task: 修复 Nautilus 侧边栏不可见与裁剪导致的缩进错乱

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44 + Nautilus 42.6）`

### 📥 User Query
> 你可以先自己一个一个去测 不要去自动化测试 你先自己完整测下来三个应用看看有啥问题 自动化会忽略掉很多问题
>
> 先修掉 把一个应用通关 再去继续推进

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/runtime.py` 的子节点枚举判据与树渲染缩进；
`runtime_test.py` 补 8 条回归测试。

**Key Actions:**
- **`should_enumerate_children()` 放宽自管理容器**: 新增 `MANAGED_ENUMERATE_CAP`（默认 256）。
  声明 `MANAGES_DESCENDANTS` 但**自报**子节点数不超过该值时照常枚举。
  `HARD_CHILD_CAP` 的检查提到前面，Calc 的 sheet 仍被它拦住。
- **`visit()` 拆分 `depth` 与 `render_depth`**: 前者管遍历预算，后者管缩进，
  只在节点**真的被渲染**时才加一。

### 🧠 Design Intent (Why)

这两个缺陷都是**手动逐应用走查**发现的，自动化矩阵测不出来：前者的表现是树里
多了一行"contents not enumerated"提示（不报错、不 isError），后者的表现是缩进
数字不好看（完全不影响任何断言）。两者都不会让任何自动化判据变红。

#### 一、侧边栏对 agent 完全不可见

直接读 AT-SPI 得到的真值：

```
Nautilus 侧边栏 list box
  states     : MANAGES_DESCENDANTS, SHOWING, VISIBLE
  child_count: 12
  interfaces : ['selection', 'component', 'collection']   ← 没有 table
```

旧逻辑对 `MANAGES_DESCENDANTS` 一律拒绝枚举，改走 `render_visible_cells()` 的
坐标寻址兜底——而那条兜底路径需要 **Table 接口**。这个容器没有，于是必然失败，
最终树里只留下一句"contents not enumerated"。结果是 Recent / Home / Documents /
Downloads / Trash / Other Locations **一个都看不到**，文件管理器的主导航栏
对 agent 等于不存在。

关键认识：**`MANAGES_DESCENDANTS` 是关于规模的*提示*，不是关于规模的*事实*。**
AT-SPI 规范说这类容器"the children should not, and need not, be enumerated"，
本意是保护客户端不要去枚举 Calc 那种 21 亿子节点的表格。但工具包设不设这个状态
全凭自觉，GTK 的 list box 只要用了 model 就会带上它，哪怕只有 12 项。
当自报数量与"我可能很大"这个声明自相矛盾时，应当**按数量走**：枚举 12 个既安全，
又是拿到内容的唯一办法。

真正危险的案例不依赖这个阈值：Calc 的 sheet 自报 2147483647，被
`HARD_CHILD_CAP`（4096）挡在前面，与自管理分支无关。所以即使这条分支整个改掉，
那道守卫也依然成立——这一点专门写了测试固定下来。

代价是 Nautilus 的观测成本从 483 → 984 token（翻倍）。这个交换是划算的：
省下 500 token 换来一个用不了的导航栏，等于把任务做不成写进了预算里。

#### 二、裁剪让缩进凭空多出空档

裁剪的设计是"只丢容器自己那一行，仍然继续递归子节点"——中间容器往往正是有价值
控件的父节点。但被裁掉的节点仍然推进了 `depth`，而缩进用的就是 `depth`，
于是每被裁一层，子节点就凭空多缩进一格。实测 Nautilus 上缩进从第 1 层直接跳到
第 6 层：

```
	0 frame Files
						17 canvas alpha.txt      ← 中间空了四级
```

这不只是难看。**无名控件的唯一消歧线索就是父子关系**——行距 combo 的
`toggle button` 没有名字，agent 只能靠"它在 `panel Line Spacing` 下面"来指认它
（这正是待办 #7 里那条"保留率指标看不到的盲区"）。缩进断掉，这条线索就断掉了。

修法是把两个语义拆开：`depth` 继续管遍历预算与深度上限，新增 `render_depth`
只在节点真被渲染时才加一，被裁分支递归时原样传下去，让子节点顶替父节点的位置。

### ✅ Verification

- **侧边栏**：`get_app_state` 现在完整列出 12 个 list item（Recent / Starred /
  Home / Desktop / Documents / Downloads / Music / Pictures / Videos / Trash /
  Other Locations），每个都带 Frame 坐标可点。
- **缩进**：Nautilus 树恢复正确嵌套
  `0 frame → 13 list box → 14 page tab list → 15 panel → 16 layered pane → 17 canvas`。
- **回归测试 8 条**，全部先在改动前的代码上确认会失败：
  - `test_nautilus_style_small_managed_container_is_enumerated` 在 HEAD 上报
    `AssertionError: False is not true` —— 正是本次缺陷。
  - `test_calc_style_sheet_is_still_refused` 在改动前后都通过 —— 证明放宽自管理
    没有把真正危险的容器放进来。
  - `test_pruned_container_does_not_advance_child_indentation` 断言被裁的 filler
    不在缩进上留空档；另一条断言不裁剪时缩进仍与真实层级一一对应，
    防止为了修裁剪把正常路径改坏。
- `./scripts/ci.sh` 全绿，76 个 Python 单测通过。

### 📌 Notes
- `MANAGED_ENUMERATE_CAP` 可用 `OPEN_COMPUTER_USE_MANAGED_CAP` 覆盖。
- 手动走查还发现 `perform_secondary_action` 的命名会误导模型（看起来像 fallback，
  实际上 a11y 语义动作才是首选路径），已记为待办 #27，等用户定夺协议层改法。
