---
name: calc-select-range-keyboard
description: In LibreOffice Calc via OCU, select a cell range with ctrl+shift+arrow jumps — the Name Box and drag_xy both fail.
metadata:
  type: feedback
---

Selecting a range in Calc through the OCU tools: neither `set_value` on the Name Box (index resolves, but Enter goes to the grid instead of committing the reference) nor `drag_xy` across the cells (synthesized drag leaves the selection unchanged) works. Only keyboard navigation does.

**Why:** the Name Box write lands in the control without the app adopting it, and the synthesized drag is too fast for Calc's mouse tracking.

**How to apply:** navigate and select with `press_key` — one key per call (`"Right Right Right"` errors: "No hardware keycode mapped"). To fill a formula down a column whose neighbours are populated, e.g. D2:D29 next to data in C2:C29:
1. enter the formula in D2, then in D29 (reach D29 via `ctrl+Down` in column C, then `Right`);
2. `ctrl+Up` to land on D29, `ctrl+shift+Up` — with D3:D28 empty this jumps straight to D2 and selects exactly D2:D29;
3. `ctrl+d` fills down from the top row of the selection.

`type_text` with a trailing `\n` does not commit a cell — follow it with a separate `press_key Return`. See [[libreoffice-uno-fallback]] when the UNO socket is available (no `--accept` on the soffice command line means it is not), and [[libreoffice-uno-changed-by-others-dialog]] for the save prompt this workflow ends on.
