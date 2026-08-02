---
name: libreoffice-uno-fallback
description: When OCU action tools fail, drive the running LibreOffice via a UNO socket from Bash instead of the GUI.
metadata:
  type: project
---

On this desktop VM the OCU accessibility/action tools (`get_app_state`, `click`, `click_xy`, `press_key`) can fail with `name 'indexer' is not defined` while `get_screenshot` and `list_apps` still work. The Bash tool runs on the *same* machine, so LibreOffice can be scripted directly.

**Why:** Screenshot-only observation is not enough to complete spreadsheet tasks, and blind coordinate clicking is unreliable.

A second, more common symptom: when soffice was launched with several documents at once, the Calc
windows sit at **2x26 px** (check with `wmctrl -lG`). The OCU tools then work perfectly but the
accessibility tree has ~29 nodes and no Name Box, formula bar, or grid to act on. Go straight to UNO.

**How to apply:** Open a UNO bridge on the already-running instance (the arg is handed to the existing process, no restart, documents stay open):

```bash
(soffice '--accept=socket,host=127.0.0.1,port=2002;urp;' &) ; sleep 4
```

Then from system `python3` (`import uno` works), resolve `uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext`, enumerate `Desktop.getComponents()` and match on `c.getURL()` — several documents may share one process. `doc.store()` saves in place keeping the original xlsx filter, no Keep-format dialog.

Gotcha: don't name a script after a stdlib module in the cwd — `inspect.py` breaks `openpyxl`, and
`enum.py` breaks `import uno` itself with a confusing circular-import traceback. Keep scripts in a
scratch dir like `/tmp/unowork/` with non-stdlib names.

Gotcha: `doc.close(True)` followed by `loadComponentFromURL` has crashed the whole soffice process (taking unrelated open documents with it). On restart, LibreOffice shows a **Document Recovery** dialog — click Discard (then Yes) when the files on disk were unmodified, since recovering marks them modified and risks overwriting on-disk edits.
