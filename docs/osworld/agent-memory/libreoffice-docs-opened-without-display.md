---
name: libreoffice-docs-opened-without-display
description: A task's "open" LibreOffice document may have no window (launched without DISPLAY); check before trying to drive it via the UI.
metadata:
  type: project
---

When a task says "this open document", the `soffice.bin` process may be running with the file yet have **no mapped window** — its `/proc/<pid>/environ` has no `DISPLAY`, so it never appears in `wmctrl -l` or on the accessibility bus.

**Why:** the harness launches the document before the desktop session is attached, so UI-driven editing silently has nothing to target.

**How to apply:** verify with `pgrep -af soffice.bin`, `DISPLAY=:0 wmctrl -l`, and `tr '\0' '\n' < /proc/<pid>/environ | grep DISPLAY`. If windowless, inspect the file first (it is usually empty), then `pkill` the orphan, `rm -f` the stale `.~lock.<name>#`, write the content with python-docx (installed, v1.2.0), and reopen with `DISPLAY=:0 setsid nohup libreoffice --writer <path>`. Expect a **Document Recovery** dialog on reopen — choose Discard, then Yes, or it restores the stale empty version over your work.

See [[web-research-via-gui-chrome]].
