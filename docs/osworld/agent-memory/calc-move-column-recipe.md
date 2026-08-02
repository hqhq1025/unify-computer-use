---
name: calc-move-column-recipe
description: Reordering Calc columns via GUI — Ctrl+Plus "insert cut cells" does not work; use insert-blank / cut / paste / delete instead.
metadata:
  type: feedback
---

To move a column in LibreOffice Calc through the OCU GUI channel, do NOT rely on the
cut-then-Ctrl+Plus ("insert cut cells") idiom. `press_key ctrl+plus` is swallowed —
xdotool synthesises `plus` as shift+equal and Calc never sees Insert Cells. Invoking
Sheet ▸ Insert Cells… from the menu with a cut clipboard also only inserts a blank
column; it does **not** paste the clipboard.

Working recipe (click the column *header* at y≈188 to select a whole column; the Name
Box confirms e.g. `E1:E1048576`):

1. Select source column header → `ctrl+x` (data disappears immediately — it's a real cut).
2. Select the destination column header → Sheet ▸ Insert Cells… (inserts one blank column, shifting right).
3. `ctrl+v` into that blank column — brings values *and* source formatting (number format, bold header).
4. Select the now-empty leftover column → Sheet ▸ Delete Columns.

Two moves can share one leftover: cut column X, paste into the blank column left by the
previous move, then delete X — net zero extra columns.

**Why:** the one-step idioms silently no-op, which reads as "the action failed" when
really the wrong channel was used.

**How to apply:** budget 4 steps per column move; verify each with the Name Box /
screenshot rather than the a11y tree, which lags a step behind after cut/paste.
Note that cutting a column can shrink row heights (auto-fit recomputes when a
substituted font leaves the sheet) — check `<row>` elements in the xlsx if heights matter.
Related: [[calc-select-range-keyboard]], [[xdotool-window-flag-ignored]],
[[libreoffice-uno-changed-by-others-dialog]].
