---
name: ocu-tools-down-use-pyatspi
description: When every OCU tool returns "name 'indexer' is not defined", drive the desktop directly from Bash via pyatspi + xdotool/wmctrl.
metadata:
  type: feedback
---

All OCU MCP tools (get_app_state, get_screenshot, click, …) can fail with the
server-side error `name 'indexer' is not defined`. It is not transient and not
app-specific — the whole plugin is down for the session.

**Why:** the MCP server's indexer failed to initialize, so no channel works;
retrying the same call just burns turns.

**How to apply:** fall back to shell tooling, which reaches the same AT-SPI and
X11 layers the plugin wraps:
- `wmctrl -l` / `xdotool search --onlyvisible --name ""` — enumerate windows,
  which is how you spot a modal dialog you cannot screenshot.
- `python3` + `pyatspi` (installed) — walk the tree and click by action:
  ```python
  import pyatspi
  for app in pyatspi.Registry.getDesktop(0):
      if 'soffice' in (app.name or '').lower():
          for w in app:
              if w.getRole() == pyatspi.ROLE_ALERT:   # modal dialog
                  ...  # recurse, find role 'push button', b.queryAction().doAction(0)
  ```
- For LibreOffice itself prefer [[libreoffice-uno-fallback]]; use pyatspi only
  for the dialogs UNO cannot reach, e.g. [[libreoffice-uno-store-blocks-on-dialog]]
  and [[libreoffice-uno-changed-by-others-dialog]].

There is no shell substitute for *seeing* the screen — `import`/`gnome-screenshot`
write a PNG you cannot view. Verify through the a11y tree and the file on disk
instead.
