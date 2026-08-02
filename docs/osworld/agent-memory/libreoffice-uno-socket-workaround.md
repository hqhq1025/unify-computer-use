---
name: libreoffice-uno-socket-workaround
description: On this desktop, synthesized mouse clicks and menu popups do not work in LibreOffice; drive it over a UNO socket instead.
metadata:
  type: project
---

On this VM (verified 2026-08-03), LibreOffice ignores XTEST pointer input entirely — the
pointer moves and `xdotool getmouselocation` reports the right window, but the app never
reacts (status-bar cursor readout stays frozen, nothing gets selected). Menu popups also
never map as X windows, so `Alt+O` / arrow keys only highlight the menubar entry.
Keyboard shortcuts and modal dialogs DO work, and `Tab` cycles shape selection.

Workaround: open a UNO bridge on the *already running* instance and script the live
document, then `doc.store()`:

```
soffice --accept="socket,host=127.0.0.1,port=2002;urp;" &   # forwarded to running pid
python3 -c "import uno; ..."   # resolve uno:socket,...;urp;StarOffice.ComponentContext
```

**Why:** GUI automation of LibreOffice here is a dead end; hours can be lost retrying clicks.
**How to apply:** For any Impress/Calc/Writer task, first try keyboard-only; the moment a
click or menu is required, switch to the UNO socket. Editing the file on disk with
python-pptx/openpyxl is a last resort — the running instance holds a stale copy and can
overwrite it.

Also note the MCP screenshot/window coordinate mapping is offset by the 37px title bar,
so pixel coordinates read off screenshots are unreliable here anyway.
