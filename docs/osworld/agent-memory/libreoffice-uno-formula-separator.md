---
name: libreoffice-uno-formula-separator
description: UNO setFormula on this box needs semicolon argument separators; commas give Err:508.
metadata:
  type: reference
---

When writing formulas through UNO `cell.setFormula()` on this machine, separate function
arguments with `;` — e.g. `=VLOOKUP(E2;$A$2:$B$7;2;0)`. Commas silently produce **Err:508**
("pair missing") in the live cell, and the saved xlsx caches it as `#VALUE!`, so the formula
text looks correct on inspection while every value is broken.

The locale here is English (Hong Kong); `setFormula` uses the UI grammar, not the API grammar.
Always read back `cell.getError()` for each written cell before calling `doc.store()`.

Related: [[libreoffice-uno-fallback]]
