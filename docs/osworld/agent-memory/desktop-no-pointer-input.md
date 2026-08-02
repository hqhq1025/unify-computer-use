---
name: desktop-no-pointer-input
description: On this OCU desktop, synthesized mouse clicks and popup menus do not work; drive apps with the keyboard.
metadata:
  type: project
---

Mouse clicks synthesized through `click_xy`/`click` (and raw `xdotool click`) are silently ignored by LibreOffice on this VM, and menu bar / context-menu popups never open (alt+accelerator, F10, Shift+F10 all fail). Keyboard input via `xdotool key` works reliably.

**Why:** Several minutes and dollars were burned re-trying clicks that reported success but changed nothing.

**How to apply:** Reach UI with keyboard only — Tab cycles shapes in Impress, F6 cycles document/toolbars/panes, Ctrl+B/U, Ctrl+]/[ for font grow/shrink. Verify effects by saving (Ctrl+S) and reading the file, not by trusting sidebar readouts (the sidebar font-size field lags one step behind the real value). The soffice window sits at screen offset (70,63) relative to screenshot coordinates if pointer input is ever needed. See [[libreoffice-semibold-font-bold-export]].
