---
name: libreoffice-no-sparkline-support
description: LibreOffice 7.3 on this VM cannot create, import, or render sparklines — write x14 extLst XML into the xlsx instead.
metadata:
  type: project
---

The desktop VM runs LibreOffice 7.3.7.2, which has **no sparkline support at all**: no `Insert > Sparkline` UI, no sparkline UNO API on `SheetCellRange`/document, and the import filter silently drops `x14:sparklineGroups` (an xlsx→ods round-trip of a file containing them yields zero sparkline elements).

**Why:** Sparklines landed after 7.3, so any GUI or UNO attempt to make them is a dead end — and opening such an xlsx in Calc and re-saving it *strips* the sparklines.

**How to apply:** Edit the xlsx directly (rezip with `zipfile`, preserving all other parts) and append, as the last child of `<worksheet>`:

```xml
<extLst><ext uri="{05C60535-1F16-4fd2-B633-F4F36F0B64E0}" xmlns:x14="...spreadsheetml/2009/9/main">
<x14:sparklineGroups xmlns:xm="...excel/2006/main"><x14:sparklineGroup displayEmptyCellsAs="gap" type="line">
  <x14:colorSeries rgb="FF376092"/>…(colorNegative,Axis,Markers,First,Last,High,Low in that order)…
  <x14:sparklines><x14:sparkline><xm:f>Sheet1!C2:E2</xm:f><xm:sqref>F2</xm:sqref></x14:sparkline>…</x14:sparklines>
</x14:sparklineGroup></x14:sparklineGroups></ext></extLst>
```

Close the doc in Calc first (see [[libreoffice-uno-fallback]]) so it can't clobber the edit. Verify with openpyxl: it warns `Sparkline Group extension is not supported and will be removed`, which confirms the structure parsed as valid — but never re-save through openpyxl either.
