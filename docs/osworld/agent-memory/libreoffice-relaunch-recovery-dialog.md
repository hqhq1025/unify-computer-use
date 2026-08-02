---
name: libreoffice-relaunch-recovery-dialog
description: Relaunching soffice from a Bash tool call needs setsid, and a killed instance triggers a Document Recovery dialog that must be discarded.
metadata:
  type: project
---

Relaunching LibreOffice from a Bash tool call on this VM:

- `nohup soffice file &` is **not** enough — the process is killed when the tool
  call returns. Use
  `setsid env GTK_MODULES=gail:atk-bridge QT_ACCESSIBILITY=1 soffice <file> </dev/null >/tmp/lo.log 2>&1 & disown`.
  Without `GTK_MODULES=gail:atk-bridge` the new instance never registers on the
  accessibility bus and the ocu tools report `appNotFound("soffice")`.
- After an instance is killed, the next start shows a **Document Recovery**
  dialog. Choose **Discard** (then **Yes**) whenever the on-disk file is already
  the desired state — recovery would restore the pre-edit autosave.
- The dialog's own window may need `xdotool windowactivate <id>` before it
  reports a proper title and exposes its buttons to AT-SPI.

**Why:** the recovery dialog silently reverts file edits made outside the GUI.

**How to apply:** after editing a document on disk, close it in LibreOffice
first, relaunch as above, and discard any recovery prompt.
Related: [[libreoffice-gui-input-broken]].
