---
name: xlsx-patch-zip-directly
description: Most reliable way to edit a Calc .xlsx here — patch the OOXML zip with python3, then reopen soffice to verify.
metadata:
  type: feedback
---

When soffice has no UNO socket (`ps` shows no `--accept=socket...`, nothing
listening on 2002) and GUI typing keeps failing, skip the UI: unzip the
.xlsx, edit the XML with python3, rezip, then relaunch
`soffice --norestore <file>` and read the result back for verification.

**Why:** soffice on this box crashes readily mid-edit (it died during a
`ctrl+1` Format Cells sequence and took an unsaved column with it — see
[[libreoffice-uno-soffice-crash-recovery]]). Patching the file on disk is
atomic and survives crashes; openpyxl 3.1.5 is installed but rewrites the
whole workbook, whereas a targeted string patch preserves it byte-for-byte.

**How to apply:** cp the file to a .bak first. Then in
`xl/worksheets/sheet1.xml`: add `<c r="C1" t="s"><v>N</v></c>` to row 1
(N = new index appended to `xl/sharedStrings.xml`, bumping both its `count`
and `uniqueCount`); add
`<c r="C2" s="1"><f>A2+B2</f><v>40557</v></c>` per data row — supply the
cached `<v>` yourself AND add `fullCalcOnLoad="1"` to `<calcPr>` in
`xl/workbook.xml` so Calc recomputes on open. Widen `<cols>`, and update
`<dimension ref>` and each row's `spans`. Reuse an existing style id for
formatting: `s="1"` there is numFmtId 14 (short date), so a date-valued
formula renders as a date with no extra work.
