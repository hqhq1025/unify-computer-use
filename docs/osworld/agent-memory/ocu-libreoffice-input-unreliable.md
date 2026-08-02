---
name: ocu-libreoffice-input-unreliable
description: On this OCU desktop, synthesized clicks and Home/End/Ctrl combos are unreliable in LibreOffice; edit the file on disk instead
metadata:
  type: project
---

On this Computer Use desktop (VM at DISPLAY=:0), driving LibreOffice text editing through
synthesized input is unreliable in two specific ways:

- `click_xy` coordinate clicks into the Impress editing canvas are silently dropped (no
  pixel or tree change), and `click` on menu bar items only sets `[selected]` without
  opening the dropdown. Clicks on **toolbar buttons and dialog buttons** via a11y actions
  do work.
- `Home` / `End` / `Ctrl+End` / `Ctrl+Home` arrive with a phantom Shift, so they extend the
  selection instead of moving the caret. Plain arrow keys (`Down`, `Right`) usually behave,
  but the phantom Shift can reappear mid-sequence. Toggling Num Lock, `xdotool keyup shift`,
  and `--clearmodifiers` do NOT fix it. Typing after this state silently replaces a large
  invisible selection and destroys document text.

**Why:** I lost most of a task to this — a caret that looked correctly placed in the
screenshot still had a live selection, so Return + typing deleted the paragraph body.

**How to apply:** For LibreOffice document edits, don't fight the input layer. Verify the
file path from `ps -o args= -p <pid>`, edit the file directly (`python-pptx` 1.0.2 and
`uno` are installed), then close the document with "Don't Save" and relaunch
`soffice --impress <file>`. Expect a **Document Recovery** dialog on relaunch — click
Discard, then Yes, or it restores the damaged autosave. Also `rm` any stale
`.~lock.<name>#` file first if soffice is not running.
