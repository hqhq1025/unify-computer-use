---
name: libreoffice-uno-changed-by-others-dialog
description: "Document Has Been Changed by Others" on save is usually a false alarm from the task setup re-copying the file; cancel and reload instead of Save Anyway.
metadata:
  type: reference
---

When `doc.store()` raises **"Document Has Been Changed by Others … Saving your version will
overwrite changes made by others"**, the usual cause on this VM is NOT a real edit conflict:
the task setup copies the fixture from `/tmp/osworld-cache/<Name>2.xlsx` over `/home/user/<Name>.xlsx`
a few seconds *after* LibreOffice already opened it. Only the mtime advanced; content matched.

**Why:** "Save Anyway" is a genuine overwrite of whatever is on disk, so it must not be a reflex —
but neither should the warning stop the task, since the divergence is normally an artifact.

**How to apply:** don't click through it blind. Diagnose from Bash first:

```bash
stat -c '%y' /home/user/<F>.xlsx; ps -o lstart= -p $(pgrep soffice.bin)   # disk newer than process?
md5sum /home/user/<F>.xlsx /tmp/osworld-cache/<F>2.xlsx                   # identical => setup re-copy
```
Also compare the in-memory used range (`cursor.gotoEndOfUsedArea(False)`; `.AbsoluteName`) with the
disk file read via openpyxl — that catches a real extra column, which is what an overwrite would eat.

Then Cancel the dialog, `.uno:Reload` the doc (set `doc.setModified(False)` first or it prompts),
re-apply the edit, and `store()` — it saves silently because the in-memory copy is back in sync.
Cancelling makes the pending `store()` fail with `ErrorCodeIOException … storeSelf: 0x11b`, which is
the *good* outcome: nothing was written.

Observed 2026-08-02: when the OCU tools were themselves broken (`name 'indexer' is not defined`, so
Cancel was unclickable), the dialog went away on its own once `timeout` killed the python client and
the URP bridge dropped — soffice aborted the pending `store()` and left the file on disk untouched.
So a dead client is an acceptable substitute for pressing Cancel; verify with `stat` and re-do the
reload/re-apply/store cycle.

Related: [[libreoffice-uno-store-blocks-on-dialog]], [[libreoffice-uno-soffice-crash-recovery]], [[libreoffice-uno-fallback]]
