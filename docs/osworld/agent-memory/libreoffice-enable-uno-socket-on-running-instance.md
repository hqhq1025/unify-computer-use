---
name: libreoffice-enable-uno-socket-on-running-instance
description: A running soffice started without --accept can still be given a UNO socket by re-invoking soffice --accept from Bash.
metadata:
  type: reference
---

When soffice is already running with just `--calc file.xlsx` (no `--accept`), you do NOT have to fall back to xdotool or zip-patching. Run:

```bash
timeout 20 soffice --accept="socket,host=localhost,port=2002;urp;" ; ss -ltnp | grep 2002
```

The second invocation hands the argument to the existing process instead of starting a new one, and that process registers the acceptor. `desktop.getCurrentComponent()` then returns the already-open document, edits included.

**Why:** most of my LibreOffice fallbacks ([[xdotool-window-flag-ignored]], [[libreoffice-basic-ide-macro-entry]], [[xlsx-patch-zip-directly]]) exist only because "there is no UNO socket". Usually there can be one.

**How to apply:** check `ps aux | grep soffice` for `--accept`; if absent, run the command above before reaching for any GUI or XML-patching workaround. Still worthless if a modal dialog is up — see [[libreoffice-crash-recovery-dialog]].
