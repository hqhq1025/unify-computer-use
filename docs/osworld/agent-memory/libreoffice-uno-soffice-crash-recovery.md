---
name: libreoffice-uno-soffice-crash-recovery
description: soffice.bin can die mid-script during UNO close/reload + chart insert; always re-check the process and relaunch with --accept
metadata:
  type: reference
---

Driving LibreOffice over UNO (see [[libreoffice-uno-fallback]]), a sequence of
`doc.close(True)` → `loadComponentFromURL` → chart insert → `store()` succeeded on
disk but left `soffice.bin` dead — every open window (25 docs) vanished, and the
OCU a11y bus stopped listing the app.

**Why:** the saved file was correct, so the crash is silent unless you check; an
OSWorld-style evaluator that does `activate_window "<file> - LibreOffice Calc"`
then Ctrl+S will fail outright if no window exists.

**How to apply:** after any UNO script that closes/reloads documents, run
`pgrep -a soffice.bin` and `wmctrl -l`. If gone, delete the stale lock and relaunch
so the UNO port comes back:

```
rm -f /home/user/.~lock.<FILE>#
cd /home/user && nohup soffice --norestore \
  --accept="socket,host=127.0.0.1,port=2002;urp;" /home/user/<FILE> >/dev/null 2>&1 </dev/null &
sleep 18
```

`.uno:Reload` is cleaner than close+reopen for a stale in-memory copy, but it is NOT safe either:
a `setModified(False)` → `.uno:Reload` → `freezeAtPosition` → `store()` run saved correctly and then
killed soffice the same way. Always re-check `pgrep`/`wmctrl` after ANY reload, not just after close.
Relaunch one document per `soffice` invocation (a few seconds apart) — see the 2x26 px window bug in
[[libreoffice-uno-fallback]] — and `wmctrl -a "<file> - LibreOffice Calc"` to pick which window
`get_screenshot` will grab, since it always returns the most recently focused one.
