## [2026-07-29 15:20] | Task: 修复 Linux 大型 accessibility tree 的遍历安全与快照质量

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64 容器（Ubuntu 22.04 + Xvfb X11 + XFCE + at-spi2-core 2.44 + LibreOffice 7.3）`

### 📥 User Query
> 在 Linux 上对 LibreOffice Calc 调 `get_app_state` 会超时。排查根因并给出适合 AT-SPI 的实现方式。

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/runtime.py` 的树遍历与快照渲染路径，以及 `main.go` 的运行时超时。

**Key Actions:**
- **尊重 `MANAGES_DESCENDANTS`**: 新增 `should_enumerate_children()`，对声明该 state 的容器不再枚举子节点，并加 `HARD_CHILD_CAP` 兜住谎报子节点数的实现。守卫同时应用于 `render_tree` / `find_first` / `iter_all`。
- **表格改坐标寻址**: 新增 `visible_cell_range()` + `render_visible_cells()`。用 `Component.get_accessible_at_point()` 打容器矩形两个对角反解可见行列范围，再用 `Table.get_accessible_at(row, col)` 逐个取单元格，替代枚举。
- **配额检查前移**: 子节点循环改为先查配额再调 `child_at()`，并加 `MAX_CHILD_FANOUT` 单容器上限。
- **`find_first` 加预算**: 新增 `FIND_FIRST_BUDGET`，此前该函数既无节点上限也无 fanout 上限。
- **未展开菜单不递归**: 菜单类角色若无 `STATE_SHOWING` 则保留节点自身但不展开子项，并标注折叠数量。
- **超时可配**: `main.go` 硬编码的 30s 改为读 `OPEN_COMPUTER_USE_RUNTIME_TIMEOUT_SECONDS`。

### 🧠 Design Intent (Why)
*根因不是"树太大"，而是 LibreOffice Calc 的 sheet 节点谎报子节点数。它的 accessible range 是整张表而非可见区（`ScRange(0, 0, nTab, MaxCol(), MaxRow(), nTab)`，`sc/source/ui/Accessibility/AccessibleSpreadsheet.cxx:247`），于是 `getAccessibleChildCount()` 返回 rows×cols = 1.7e10，实测 `child_count` 读到 **1073741824**。上游自己在 `AccessibleTableBase.cxx:274` 的注释里写着 `FIXME: representing rows & columns this way is a plain and simple madness.` 朴素 DFS 掉进这个节点不是"慢"，是永远不会结束——实测单个容器需 22–36 小时。*

*AT-SPI 规范对此有正式契约：`MANAGES_DESCENDANTS` 的定义原文是 "the children should not, and need not, be enumerated by the client"，而 Calc 的 sheet 正是这么标记的（`AccessibleSpreadsheet.cxx:1066`）。所以正解是尊重该契约并改用坐标寻址，而不是加大配额或做客户端裁剪。*

*配额前移是比 state 守卫更底层的兜底：`visit()` 开头虽然查配额并立刻 return，但 `for i in range(child_count)` 这个循环本身不会停，光是发起注定被丢弃的 `child_at()` 往返就足以挂死。前者依赖容器正确声明 state，后者不依赖任何声明。*

*菜单折叠解决的是另一类问题——不是超时而是**配额被挤占**。实测默认配额 1200 下 LibreOffice 的菜单树占掉 100%（一份完整菜单栏约 780 节点），表格单元格一个都进不来，即使不超时返回的也全是菜单。只对菜单类角色应用该规则：其它中间层容器（panel / scroll pane / viewport）在 LibreOffice 上普遍不设 `SHOWING`，一并过滤会把整棵树砍空——这是先前一版实现踩过的坑。*

*两道守卫都只读 libatspi 的本地缓存（`ATSPI_CACHE_DEFAULT` 覆盖 STATES 与 CHILDREN），不产生 D-Bus 往返，因此可以直接放在遍历热路径上。相比之下 `get_extents` / `get_n_actions` 才是真往返，先前一版用 `get_extents` 做离屏判据反而把遍历拖慢了一个数量级。*

### ✅ Verification
Ubuntu 22.04 + Xvfb(X11) + at-spi2-core 2.44 容器，LibreOffice Calc 7.3 打开一个 30 行 × 4 列的 xlsx。

超大容器守卫（同一 Calc 实例，仅替换二进制）：

| 配额 | 无守卫 | 有守卫 |
| --- | --- | --- |
| 1200 | 895 ms ✓ | 1043 ms ✓ |
| 5000 | **75 s 超时，0 节点** ✗ | 1386 ms，1894 节点 ✓ |

该缺陷在小配额下不暴露：遍历在走到 sheet 节点之前就被 `max_tree_nodes` 截断了。因此 `tests/test_large_container_guard.py` 固定使用大配额，并已双向验证——打了守卫 PASS，回退到未打守卫的二进制则 FAIL。

菜单折叠 + 表格寻址后（同一实例）：

| 配额 | 耗时 | 节点 | 单元格 |
| --- | --- | --- | --- |
| 1200 | 1060 ms | 1200 | 622（改前 0） |
| 5000 | 1285 ms | 1671 | 777 |
| 20000 | 1291 ms | 1671 | 777 |

配额提高 16 倍而耗时几乎不变，说明遍历已由内容驱动而非配额驱动。

单元格内容逐格比对源文件一致：

```
R0: ['', 'Level', 'Student', 'Subject', 'Marks']
R1: ['', 'Primary', 'Blake Dreary', 'English', '36']
R2: ['', '', '', 'Urdu', '83']
```

777 个单元格中 703 个为空，与源文件一致。单元格值只取 text 不回退 `numeric_value`——Calc 空单元格的 Value 接口返回 `0.0`，会让空白单元格看起来像填了 0。

### ⚠️ Known Limitations
- 菜单折叠的代价：未展开菜单的子项不再进入快照，需要先对父菜单 `click` 展开再重新取快照，才能拿到 "Save" 之类的 `element_index`。折叠前可以直接对离屏菜单项 `do_action`。设 `OPEN_COMPUTER_USE_INCLUDE_OFFSCREEN=1` 可恢复旧行为。
- `visible_cell_range()` 依赖 `get_accessible_at_point()`，若应用未实现该方法则回退到"不枚举"并在树中显式说明。
- 未验证 Writer / Impress 及非 LibreOffice 的表格类应用。
