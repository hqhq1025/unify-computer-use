# agent 自己写下的操作笔记

这 41 个文件不是我写的，是**跑测中的 cc 自己攒的**。

它们本来存在 `~/.claude/projects/-tmp-ocu-agent-run/memory/`。工作目录每题相同，
所以记忆路径也相同——跑到第 152 题时发现那里已经攒了 41 个文件，
而第 108 题起的约 60 次跑测都可能读到它们。**题与题不再独立。**

比"作弊"更值得注意的是另一层：这些笔记里有一批是**冲着我们这套 MCP 写的缺陷报告**，
而 agent 一旦记住绕路方法，那些缺陷就**再也不会出现在轨迹的报错里**——
工具的问题被 agent 的记忆吸收掉了，从数据上看会显得一切正常。

所以先归档、再清除：`osworld-bench.py` 现在每题跑之前把这个目录拷进这里再删掉。

## 直接指向本 MCP 的几条

| 文件 | agent 的说法 | 我的核实 |
|---|---|---|
| `ocu-type-text-mangles-plus-and-newlines` | type_text 把 `+` 变成 `=`、吞掉换行、还转小写 | **不成立**。在 gedit 上实测（能隔离出工具自身行为）`=A2+B2\nHello World` 原样落下：`+` 是 `+`、大写保留、换行保留。它遇到的是 Calc 自己的公式解析，以及"单元格编辑器里换行不提交行"的语义 |
| `ocu-tools-down-use-pyatspi` | 每个 OCU 工具都报 `name 'indexer' is not defined` | **成立**，已修（render_visible_cells 漏传 indexer，Calc 表格渲染路径一走就崩） |
| `impress-position-size-spinbuttons-ignore-a11y` | set_value 报成功但没到文档 | 待核实 |
| `options-dialog-spinbutton-setvalue-reverts` | set_value 读回正常、点 OK 后 revert | 待核实 |
| `verify-render-via-pdf-export` | a11y 桥在 soffice 上超时 | 与已知的 30s 运行时超时一致 |
| `xdotool-window-flag-ignored` | LibreOffice 忽略 xdotool `--window` | 与我们"XTEST 是全局投递"的设计一致，不是缺陷 |

**"agent 说是缺陷"和"确实是缺陷"是两回事。** 上面第一条就是反例：
它的结论合理、证据具体，但换个应用就复现不出来——真正的原因在 Calc 那边。
所以这一栏必须逐条核实过再往里填，不能照抄。
