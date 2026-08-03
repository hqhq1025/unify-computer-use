---
name: libreoffice-bulk-cell-entry
description: On this desktop VM, fill LibreOffice Calc cells in bulk via the UNO socket API — type_text and clipboard paste both fail.
metadata:
  type: reference
---

Filling many LibreOffice Calc cells on this VM: two obvious routes are broken.
`type_text` strips tab and newline characters, so a TSV row lands concatenated in
one cell; and `Ctrl+V` from an xclip-owned clipboard raises "The contents of the
clipboard could not be pasted" regardless of target type or whether xclip stays
alive.

What works: enable a UNO listener on the already-running instance with
`soffice --accept="socket,host=localhost,port=2002;urp;"` (it attaches to the
existing pid, no restart), then connect from `python3 -c "import uno"` via
`com.sun.star.bridge.UnoUrlResolver`, find the doc by URL in
`desktop.Components`, and use `sheet.getCellByPosition(col, row).setValue(...)`
followed by `doc.store()` to save in place in the original format.

**Why:** it edits the live open document precisely, so the user's window shows
the result and no format conversion happens on save.

**How to apply:** reach for this whenever a desktop task needs more than a
handful of cells written into an open Calc document. See also
[[libreoffice-startup-hang]].
