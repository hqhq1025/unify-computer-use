---
name: libreoffice-pivot-xlsx-export-gaps
description: LibreOffice's xlsx pivot export drops "% of total" and grand-total settings; patch the pivotTable XML afterwards.
metadata:
  type: reference
---

A DataPilot built over UNO looks right on screen and its **cached** cell values export fine, but
LibreOffice's `xl/pivotTables/pivotTableN.xml` writer silently omits two things. Both only bite
after a **reload** (or when Excel refreshes), so verify by File > Reload, not by reading the cells
you just wrote.

1. **`showDataAs` is never written.** Setting `f.Reference` to a
   `com.sun.star.sheet.DataPilotFieldReference` with `ReferenceType = TOTAL_PERCENTAGE` gives correct
   percentages in the sheet, but the exported `<dataField>` has no `showDataAs`, so a refresh reverts
   to raw counts. Note `f.Reference` starts as **None** — build it with
   `uno.createUnoStruct("com.sun.star.sheet.DataPilotFieldReference")`, don't read-modify-write it.
2. **`desc.RowGrand = desc.ColumnGrand = False` is not exported.** The OOXML defaults for
   `rowGrandTotals`/`colGrandTotals` are *true*, so on reload LibreOffice adds a "Total Result" row to
   every table — which silently eats the blank separator rows in a stacked layout.

Patch both by rewriting the zip (openpyxl can't touch pivot parts):

```python
'<pivotTableDefinition ' -> '<pivotTableDefinition rowGrandTotals="0" colGrandTotals="0" '
'<dataField name="Count - X" fld="0" subtotal="count" numFmtId="165"/>'
  -> '<dataField name="Percentage" fld="0" subtotal="count" showDataAs="percentOfTotal" baseField="0" baseItem="0" numFmtId="165"/>'
```

**The patch is destroyed by any later `doc.store()`.** LibreOffice re-exports every
`xl/pivotTables/*.xml` part from its in-memory model on each save, so a cosmetic follow-up edit
(column widths, a tweaked caption) silently reverts `showDataAs` and the grand-total flags. Do all
Calc-side edits first, save once, then patch — or keep the patch script idempotent and re-run it
after every store(). Note the open GUI document still holds the *unpatched* model afterwards; don't
try to sync it with File > Reload, which has killed soffice (see [[libreoffice-uno-soffice-crash-recovery]]).

Renaming the data field in the XML is also the only way to relabel the `Count - <field>` caption —
the UNO `DataPilotField.Name` is read-only. Overwriting the caption cell from Python works too and
survives the save, but the two must be kept consistent.

To get a percentage at all you need a second field as the DATA field: use a numeric id column
(e.g. `Respondents`) with `Function = COUNT`, since one field can't be both ROW and DATA.

Related: [[libreoffice-uno-pivot-table]], [[libreoffice-uno-fallback]], [[libreoffice-uno-changed-by-others-dialog]]
