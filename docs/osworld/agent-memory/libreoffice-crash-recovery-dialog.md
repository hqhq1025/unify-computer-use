---
name: libreoffice-crash-recovery-dialog
description: After a soffice crash, relaunch shows a modal Document Recovery dialog that blocks UNO (getCurrentComponent returns None); discard it via xdotool
metadata:
  type: reference
---

soffice can die on a plain GUI **Ctrl+S** too, not just during UNO close/reload —
observed right after applying Format Cells in Calc. Symptom is the same as
[[libreoffice-uno-soffice-crash-recovery]]: OCU reports `appNotFound("soffice")`
and `pgrep -a soffice` returns nothing. Unsaved GUI edits are lost; check the
file mtime before assuming anything landed.

**Why:** on relaunch, LibreOffice puts up a modal *Document Recovery* dialog.
While it is up, `desktop.getCurrentComponent()` returns `None`, so a UNO script
fails with `AttributeError: 'NoneType' object has no attribute 'Sheets'` — which
reads like a bad script, not a blocked UI. Worse, soffice often drops off the
a11y bus at that moment, so OCU `click` cannot dismiss it either.

**How to apply:** when UNO returns None right after a relaunch, check for the
dialog with `wmctrl -l | grep -i recovery` before debugging the script. Dismiss
it with xdotool, converting OCU's window-relative coords (which include the
~37 px title bar) to absolute via `xwininfo`:

```
xwininfo -id <winid> | grep Absolute        # client-area origin
xdotool windowactivate <winid>; sleep 0.5
xdotool mousemove <abs_x> <abs_y> click 1   # Discard
```

Discard raises a second **Question** alert ("Are you sure...") — the a11y bus
usually comes back by then, so finish with an OCU click on *Yes*. Discarding is
safe when the on-disk file is intact; you then reapply the edit over UNO, which
is more reliable than the GUI path that crashed.
