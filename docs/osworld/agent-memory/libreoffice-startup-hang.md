---
name: libreoffice-startup-hang
description: LibreOffice on this VM sometimes deadlocks at startup with a process but no window; kill it, clear the lock file, relaunch.
metadata:
  type: reference
---

LibreOffice can come up on this VM with `soffice.bin` running but never mapping a
window and never appearing on the accessibility bus. Diagnostic: the main thread
sits in `futex_wait_queue` (check `/proc/<pid>/task/*/wchan`) with 0:00 CPU time
after minutes.

Recovery: `pkill -9 -f soffice`, delete the stale `.~lock.<file>#` next to the
document, relaunch with `setsid libreoffice --calc <file>`. A Document Recovery
dialog follows; Discard is safe when the document was never displayed, since
there were no unsaved changes.

**Why:** waiting does not resolve it — the process is deadlocked, not slow.

**How to apply:** if `get_app_state` cannot find soffice but `ps` shows it,
check wchan before assuming the app is still loading. See also
[[libreoffice-bulk-cell-entry]].
