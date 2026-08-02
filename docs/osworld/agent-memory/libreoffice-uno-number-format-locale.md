---
name: libreoffice-uno-number-format-locale
description: NumberFormats.addNew() parses the format code in the TARGET locale's own separators, so "0.0" silently means something else in ru/de.
metadata:
  type: reference
---

To make cells display a comma decimal separator, apply a number format registered under a
locale that uses one (Russian = LCID 419, exported to xlsx as `[$-419]0.0`):

```python
from com.sun.star.lang import Locale
formats = doc.getNumberFormats(); loc = Locale("ru", "RU", "")
key = formats.queryKey("0,0", loc, False)
if key == -1: key = formats.addNew("0,0", loc)   # NOTE: comma, not "0.0"
sheet.getCellRangeByName("A2:B20").NumberFormat = key
```

The format code must be written with the separators of the locale you pass, not with "."
`addNew("0.0", ru_RU)` SUCCEEDS and returns a valid key — but "." is ru's *thousands*
separator, so 0.1 renders as "0.0" and 1 as "0.1". No error, just wrong output.
Always read back `cell.getString()` for a couple of cells to confirm the display.

`queryKey` returns -1 for codes not already registered, including plain "0.0"/"0,0" — so
always fall through to `addNew`. This survives an xlsx round-trip as `[$-419]0.0`, and
`libreoffice --convert-to csv` then prints the comma form.

Related: [[libreoffice-uno-fallback]], [[libreoffice-uno-formula-separator]]
