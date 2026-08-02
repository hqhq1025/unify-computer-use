---
name: ocu-type-text-mangles-plus-and-newlines
description: OCU type_text into LibreOffice turns "+" into "=" and swallows newlines, dumping everything into one cell.
metadata:
  type: feedback
---

`mcp__ocu__type_text` is unreliable for spreadsheet formulas. Sending
`Maturity Date\n=A2+B2\n=A3+B3\n...` landed as the single literal string
`Maturity Date=a2=b2=a3=b3...` in one cell: every `+` became `=`, every
newline vanished, and the case was lowered.

**Why:** its keyboard fallback path does not map shifted/symbol keysyms or
control characters correctly.

**How to apply:** press Escape to cancel the botched cell edit, then drive
the keys from Bash with xdotool instead — `xdotool type --delay 40 "=A2+B2"`
followed by `xdotool key Return`, looping one cell per iteration. That
renders `+` and Return correctly. See [[xdotool-window-flag-ignored]] (use
global XTEST, no `--window`) and [[calc-select-range-keyboard]].

Note `xdotool mousemove x y click 1` uses SCREEN-absolute coordinates while
OCU frames are WINDOW-relative; a mismatch silently leaves the cell cursor
where it was, so a following `ctrl+shift+Down` selects the wrong range.
Prefer deterministic keyboard navigation (`ctrl+Home`, then arrows).
