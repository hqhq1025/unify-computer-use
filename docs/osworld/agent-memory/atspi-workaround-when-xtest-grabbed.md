---
name: atspi-workaround-when-xtest-grabbed
description: On this desktop, GNOME Shell can hold a global pointer+keyboard grab that silently swallows all synthetic clicks/keys; drive apps through AT-SPI directly instead.
metadata:
  type: project
---

Observed 2026-08-03 on this VM (GNOME 42 / mutter, DISPLAY=:0): every XTEST-synthesized
click and keypress was accepted by the X server (the pointer really moved) but never
reached any application — not LibreOffice's canvas, not its menu bar, not even window
focus changes on other apps. Diagnosis: some client holds a global grab. Confirm with

```python
from Xlib import display, X
d = display.Display()
print(d.screen().root.grab_pointer(True, X.ButtonPressMask, X.GrabModeAsync,
      X.GrabModeAsync, X.NONE, X.NONE, X.CurrentTime))  # 1 == AlreadyGrabbed
```

Escape does not clear it, and `org.gnome.Shell.Eval` is disabled, so the grab cannot be
released from the outside.

**Why:** every Computer Use tool that ends in `[synthesis]` — click_xy, drag_xy,
press_key, and the coordinate fallback inside `click` — is dead in this state, and it
fails *silently*: the tool reports success while the tree and pixels stay identical.

**How to apply:** when two or three synthesized actions in a row report "nothing on
screen changed", stop retrying and switch to the pure-AT-SPI path via `python3` +
`pyatspi` (installed). That channel is unaffected by the grab. Working recipe used for
LibreOffice Writer:

- select text: walk to the `document text` node, take the target paragraph child, then
  `t = para.queryText(); t.setCaretOffset(0); t.addSelection(0, t.characterCount)` —
  this moves the real document cursor (status bar confirmed "Selected: 62 words").
- run a command: find the toolbar control by name/role and `n.queryAction().doAction(0)`.

Prefer OCU's own `click`/`invoke_element_action` on elements that show `[has-click-action]`
first, since those also use AT-SPI; drop to the Python script when the element exposes no
action and `click` would fall back to coordinates.
