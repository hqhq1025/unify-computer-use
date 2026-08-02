---
name: libreoffice-basic-ide-macro-entry
description: How to type and run a Basic macro in LibreOffice's Basic IDE when soffice was started without a UNO --accept socket.
metadata:
  type: reference
---

When soffice is running with no `--accept=socket` (check `/proc/<pid>/cmdline`), [[libreoffice-uno-fallback]] doesn't apply — drive it through Tools > Macros > Edit Macros instead. Four traps, all hit in one session:

1. **`type_text` lands in the Watch box, not the code editor.** Its AT-SPI editable-text write picks the Watch field even when the editor has focus and the caret is visibly in the code (status bar showed `Ln 11, Col 34`). Clear the Watch field with `set_value <watch index> ""` and type via xdotool instead.
2. **`xdotool type` silently drops `\n`** in the Basic editor — the whole macro lands on line 1 concatenated. Feed it line by line: `while IFS= read -r l; do xdotool type -- "$l"; xdotool key Return; done < file`. (Related: [[xdotool-window-flag-ignored]].)
3. **A variable named `iF` is a syntax error** ("BASIC syntax error. Symbol expected." on the `Dim` line). Basic is case-insensitive, so `iF` parses as the `If` keyword. Use `nF` for a FreeFile handle.
4. **F5 and the Run toolbar button open the "Basic Macros" picker** rather than running. Just use it: expand My Macros > Standard > Module1, click the module (this fills Macro Name), then Run.

For title-casing / whitespace work, skip hand-rolled Basic string loops — call the sheet functions:
```basic
oFA = createUnoService("com.sun.star.sheet.FunctionAccess")
s = oFA.callFunction("TRIM", Array(s))    ' trims ends AND collapses internal runs
s = oFA.callFunction("PROPER", Array(s))  ' matches Python str.title(), incl. after hyphens
```
Write progress to a log file with `Print #nF, ...` rather than `MsgBox` — a MsgBox is modal and blocks the session. `oDoc.store()` may raise the changed-by-others prompt ([[libreoffice-uno-changed-by-others-dialog]]).
