---
name: libreoffice-headless-instance-quirk
description: On this desktop a soffice.bin process runs with no visible window, so new soffice launches hand off to it invisibly
metadata:
  type: project
---

A `soffice.bin` process is often already running on this machine with **no X window** (checked via `wmctrl -l`). Any new `soffice <file>` launch hands the document off to that instance and exits, so the file opens but nothing appears on screen.

**Why:** LibreOffice reuses a single instance per user profile; the existing one is stuck/headless.

**How to apply:** Launch with a separate profile instead of killing the stuck process:
`DISPLAY=:0 soffice -env:UserInstallation=file:///tmp/lo_alt_profile --norestore --writer <file>`
This spawns a real window. Expect a "Document in Use / locked by yourself" dialog if the invisible instance already grabbed the file — choose **Open** to edit. Also expect the first-run "Tip of the Day" dialog and, on Ctrl+S, the "Keep current format" prompt.
