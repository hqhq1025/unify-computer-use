---
name: libreoffice-uno-line-chart-recipe
description: Recipe for a line chart over non-adjacent columns via UNO, including where the chart's position actually lives.
metadata:
  type: reference
---

`sheet.Charts.addNewByName(name, rect, ranges, True, True)` accepts a TUPLE of `com.sun.star.table.CellRangeAddress`, so X-category and Y-value columns need not be adjacent — pass `(A1:A36, E1:E36)`. The two trailing booleans are ColumnHeaders/RowHeaders; both True makes row 1 the series name and column A the categories.

Then swap the diagram: `chart.Diagram = chart.createInstance("com.sun.star.chart.LineDiagram")`, with `SymbolType = -3` for lines-only. Exports to xlsx as a real `<c:lineChart>` with `<c:cat>` pointing at the category range.

**Gotcha:** the chart object returned by `Charts.getByName()` has NO `setPosition`. To move it, walk `sheet.DrawPage` and `setPosition()` on the shape there. The `rect` passed to `addNewByName` is only the initial placement.

Related: [[libreoffice-uno-chart-datarowsource]], [[libreoffice-enable-uno-socket-on-running-instance]].
