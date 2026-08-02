---
name: xdotool-window-flag-ignored
description: LibreOffice ignores xdotool key/type --window; use global XTEST synthesis instead.
metadata:
  type: feedback
---

Driving LibreOffice from Bash with `xdotool key --window $WID ...` / `xdotool type --window ...` silently does nothing — the call succeeds, the document is untouched. Drop `--window` so xdotool uses XTEST global synthesis (with `DISPLAY=:0`), after confirming the target is frontmost via `xdotool getactivewindow getwindowname`.

**Why:** `--window` sends XSendEvent synthetic events, which LibreOffice/VCL discards; XTEST events are indistinguishable from real input.

**How to apply:** Bash + xdotool is a much cheaper way to type long formulas and key sequences than one OCU `press_key` per keystroke (each returns a full accessibility tree). Batch the keys in one Bash call with `sleep 0.2`-`0.4` between them, then confirm with a single `get_screenshot`. See [[ocu-tools-down-use-pyatspi]] and [[calc-select-range-keyboard]].
