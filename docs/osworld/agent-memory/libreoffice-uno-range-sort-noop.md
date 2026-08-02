---
name: libreoffice-uno-range-sort-noop
description: UNO createSortDescriptor()+range.sort() silently does nothing; reorder rows via getDataArray/setDataArray instead.
metadata:
  type: project
---

Sorting a Calc range over the UNO bridge with the documented recipe — `sd = rng.createSortDescriptor()`, mutate the `SortFields` / `ContainsHeader` `PropertyValue`s in the returned sequence, then `rng.sort(sd)` — **silently no-ops**. No exception, no error; `getDataArray()` right after shows the original order, and `doc.store()` happily writes the unsorted file. Mutating structs inside the sequence pyuno returns does not write back into the descriptor.

**Why:** The failure is invisible from the script, so it looks like a successful save. Verify by re-reading the saved file with openpyxl, not by trusting `store()`.

**How to apply:** Reorder the values yourself and write them back in one shot:

```python
rng  = sheet.getCellRangeByPosition(0, 0, endcol, endrow)   # endrow/endcol from cursor.gotoEndOfUsedArea(False)
data = [list(r) for r in rng.getDataArray()]
header, body = data[0], data[1:]                            # skip the header row
body.sort(key=lambda r: r[3])                               # 0-based column within the range
rng.setDataArray(tuple([tuple(header)] + [tuple(r) for r in body]))
```

`getDataArray` gives dates as serial floats and `setDataArray` writes them back as floats, so date columns survive — the number format lives on the cell and does not travel with the value. That is only safe when the format is uniform down the column; check with `set(sheet.getCellByPosition(col, r).NumberFormat for r in ...)` first. Same caveat for any per-row styling: this moves values only, never formats.

See [[libreoffice-uno-fallback]] for opening the socket, and [[libreoffice-uno-store-blocks-on-dialog]] — a "Document Has Been Changed by Others" prompt can block the `store()` that follows.
