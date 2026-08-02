---
name: impress-uno-insert-table
description: Inserting a table in Impress — the Insert Table dialog crashes soffice; build the TableShape over UNO and set row/column sizes explicitly.
metadata:
  type: feedback
---

Insert > Table in Impress: typing into "Number of columns" worked, but the very next
`click` on the "Number of rows" spin button crashed soffice outright (30s tool timeout,
then the Document Recovery dialog). Do it over UNO instead:

```python
tbl = doc.createInstance("com.sun.star.drawing.TableShape")
page.add(tbl)                       # add BEFORE touching .Model
tbl.setPosition(Point(x, y)); tbl.setSize(Size(w, h))
m = tbl.Model                       # starts at 1 row x 1 column, not 2x2
m.Rows.insertByIndex(m.Rows.getCount(), n_rows - 1)
m.Columns.insertByIndex(m.Columns.getCount(), n_cols - 1)
for i in range(m.Columns.getCount()): m.Columns.getByIndex(i).Width  = w // n_cols
for i in range(m.Rows.getCount()):    m.Rows.getByIndex(i).Height    = h // n_rows
```

**Why:** the width/height loop is not optional. insertByIndex gives the original row/column
the whole size and every new one 0, and that survives into the pptx —
`<a:gridCol w="9071280"/><a:gridCol w="0"/>` and four `<a:tr h="0">`. Verify in the saved
XML, not on the Impress canvas: the canvas keeps a stale uneven layout even when the model
and the export are both even, and a table with no borders shows no row separators at all.

**How to apply:** get a UNO socket up first ([[libreoffice-enable-uno-socket-on-running-instance]]),
then check `ppt/slides/slideN.xml` for the a:gridCol / a:tr counts and sizes.
Crash cleanup: [[libreoffice-crash-recovery-dialog]]. Saving: [[libreoffice-uno-store-blocks-on-dialog]].
