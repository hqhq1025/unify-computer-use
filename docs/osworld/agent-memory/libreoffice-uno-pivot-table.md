---
name: libreoffice-uno-pivot-table
description: How to build a real pivot table (DataPilot) in LibreOffice Calc over UNO, and the row-layout it produces.
metadata:
  type: reference
---

Pivot tables can be created headlessly over the UNO bridge, and LibreOffice **does** export them
to xlsx as genuine `xl/pivotTables/` + `xl/pivotCache/` parts — so a task demanding "use the Pivot
Table feature" is satisfiable without touching the GUI wizard.

Recipe on a target sheet `s2`:

```python
from com.sun.star.sheet.DataPilotFieldOrientation import COLUMN, ROW, DATA, HIDDEN
from com.sun.star.sheet.GeneralFunction import SUM
from com.sun.star.table import CellRangeAddress, CellAddress

desc = s2.DataPilotTables.createDataPilotDescriptor()
desc.setSourceRange(CellRangeAddress(Sheet=src_sheet_idx, StartColumn=0, StartRow=0,
                                     EndColumn=last_col, EndRow=last_row))
desc.ShowFilterButton = False        # else a "Filter" button cell lands in the output
desc.ColumnGrand = desc.RowGrand = False
for i in range(desc.getDataPilotFields().Count):
    f = desc.getDataPilotFields().getByIndex(i)
    f.Orientation = {"Promotion": COLUMN, "Revenue": DATA}.get(f.Name, HIDDEN)
    if f.Name == "Revenue": f.Function = SUM
s2.DataPilotTables.insertNewByName("Name", CellAddress(Sheet=..., Column=0, Row=0), desc)
```

Every field you don't want must be set `HIDDEN` explicitly — unset fields are not ignored.
Get the source extent from `cursor.gotoEndOfUsedArea(False)`, not a hardcoded range.

**Prefer ROW orientation for a simple "percentage of <field>" table.** A ROW field wastes no
caption row — the output is exactly `1 header row + 1 row per member`, 2 columns wide:

```
A2 "Sex"        B2 "Count - Respondents"   <- overwrite B2 to rename the caption
A3 "Female"     B3 42.50%
A4 "Male"       B4 57.50%
```

So `getOutputRange()` height is predictable and stacking several pivots with exactly one blank row
between them is just `next_start = r.EndRow + 2`. Always read the real extent from
`getOutputRange()` after each insert rather than assuming a member count.

**Layout gotcha:** a COLUMN field always burns the top row on its own field caption. Inserting at
A1 with one column field + one data field yields row1=`"Promotion"`, row2=the member names,
row3=the sums. There is no setting to suppress that caption row, and deleting row 1 afterwards
would leave the exported `<location ref="A1:C3"/>` pointing at shifted cells. So if a task says
"names as the column headers", the names legitimately land in **row 2** — don't fight it.

Related: [[libreoffice-uno-fallback]], [[libreoffice-uno-store-blocks-on-dialog]], [[libreoffice-uno-formula-separator]]
