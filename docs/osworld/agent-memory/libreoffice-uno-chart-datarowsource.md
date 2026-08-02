---
name: libreoffice-uno-chart-datarowsource
description: UNO charts default to column-wise series; a single-row data range needs Diagram.DataRowSource = ROWS.
metadata:
  type: reference
---

`Sheet.Charts.addNewByName(name, rect, ranges, colHeaders, rowHeaders)` defaults to reading series
**by column**. Charting one horizontal row (e.g. a `Total` row across Jan..Jun) therefore silently
produces 6 single-point series named Jan..Jun with one category "Total" — it looks plausible on
screen but the saved xlsx has the wrong shape.

**Why:** the resulting `<c:ser>` refs are `$B$12`,`$C$12`,… instead of one series over `$B$12:$G$12`,
so any checker reading series/categories fails.

**How to apply:** after `setDiagram(...)`, set

```python
from com.sun.star.chart.ChartDataRowSource import ROWS
chart.Diagram.DataRowSource = ROWS
```

Then one series named from the row header spans the month columns. Non-contiguous ranges work —
pass several `CellRangeAddress` values (e.g. `A1`,`C1:G1`,`A13`,`C13:G13`) to skip a column.
Chart type: `com.sun.star.chart.BarDiagram` with `Vertical = False` gives vertical columns;
`LineDiagram` with `Lines = True` gives a line chart. Title via `HasMainTitle = True` +
`Title.String`. See [[libreoffice-uno-fallback]].
