---
name: libreoffice-mouse-input-dead
description: On this desktop, synthesized mouse clicks never reach LibreOffice; keyboard works, and UNO over a socket is the reliable way to edit documents.
metadata:
  type: project
---

Pointer input (both `click_xy`/AT-SPI synthesis and raw `xdotool mousemove/click`) is silently
ignored by LibreOffice on this VM — menus never open, sidebar fields never focus, slide-panel
thumbnails never activate. Keyboard synthesis (`xdotool key`, `press_key`) does work: Page Up/Down
to change slides, Tab to cycle shapes, F4 for Position and Size. Writing a spin button through the
AT-SPI value API does NOT commit; the field must be focused by Tab, typed into, then Enter pressed.

For anything the keyboard cannot reach (font size lives only in the sidebar/Format menu), relaunch
with `soffice --accept="socket,host=localhost,port=2002;urp;" <file>` and drive it from `python3`
with the `uno` module (python3-uno is installed), then `doc.store()`. Editing the file with
python-pptx instead is riskier when the expected result was produced by LibreOffice: a round-trip
through LibreOffice shifts every shape width/height by ~360 EMU, and a size comparison would flag
a python-pptx-written file.

**Why:** two long GUI attempts stalled on dead clicks, and one of them ended with soffice crashing.
**How to apply:** reach for keyboard paths first, drop to UNO as soon as a control needs a click.
