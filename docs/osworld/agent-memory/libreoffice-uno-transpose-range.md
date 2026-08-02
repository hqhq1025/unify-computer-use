---
name: libreoffice-uno-transpose-range
description: Transpose a Calc range over UNO by calling sheet.copyRange() once per cell — keeps values and formatting; Paste Special/transpose needs a GUI dialog.
metadata:
  type: reference
---

LibreOffice 7.3 has no `.uno:PasteTransposed` dispatch, and Paste Special's Transpose checkbox needs
a modal dialog. Instead transpose cell-by-cell with `XCellRangeMovement.copyRange(destCellAddress,
srcRangeAddress)` — a one-cell source range per call, so 4x5 = 20 calls. copyRange carries the cell's
formatting (background, bold, borders, number format) along with the value, which `setDataArray`
does not.

```python
from com.sun.star.table import CellRangeAddress, CellAddress
idx = sh.RangeAddress.Sheet
for a in range(nrows):
    for b in range(ncols):
        src = CellRangeAddress(); src.Sheet = idx
        src.StartRow = src.EndRow = r0 + a
        src.StartColumn = src.EndColumn = c0 + b
        dst = CellAddress(); dst.Sheet = idx; dst.Row = R0 + b; dst.Column = C0 + a
        sh.copyRange(dst, src)
```

**Why:** the header column's blue fill lands on the transposed header *row* automatically, which is
what a real Paste Special transpose produces.

Watch out that destination and source ranges don't overlap. Related: [[libreoffice-uno-fallback]].
