---
name: libreoffice-gui-input-broken
description: In this desktop VM, LibreOffice ignores all mouse input and never renders dropdown menus; only keyboard input works.
metadata:
  type: project
---

On this VM (Ubuntu + LibreOffice 7.3, Impress), LibreOffice accepts **keyboard**
input only. Verified 2026-08-02:

- Mouse clicks (both `click_xy` and raw `xdotool click` at correct root
  coordinates) are delivered to the window but ignored — clicking a slide
  thumbnail, a view tab, or a menu bar entry does nothing.
- Menu bar entries can be *selected* via `F10` + arrow keys, but the dropdown
  popup never maps. Confirmed by full-screen diff: only the 25px menu-bar strip
  changes, and `xwininfo -root -children` shows no popup window.
- Consequence: Format > Character, color pickers, and every other popup-based
  control are unreachable. Keyboard shortcuts (Page Up/Down, Ctrl+A, Ctrl+W)
  and the AT-SPI `click` action on *dialog* buttons do work.

**Why:** several minutes were burned re-trying clicks that could never land.

**How to apply:** for LibreOffice formatting tasks here, skip the GUI — edit the
document file directly (`python-pptx` / `openpyxl` are installed), then close the
open document and relaunch `soffice <file>`. Note the window is offset (70, 27)
from root coordinates if raw xdotool is needed. See
[[libreoffice-relaunch-recovery-dialog]].
