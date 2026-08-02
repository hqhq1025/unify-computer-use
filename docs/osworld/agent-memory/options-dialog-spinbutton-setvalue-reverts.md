---
name: options-dialog-spinbutton-setvalue-reverts
description: set_value on an Options-dialog spin button reads back fine but reverts on OK; click, type, then Tab.
metadata:
  type: feedback
---

In the Tools > Options dialogs, `set_value` on a spin button (e.g. the AutoRecovery "minutes"
field) updates the control and reads back the new number — but OK commits the OLD value. The
config file still showed 10 after setting 3 and clicking OK.

Working sequence: `click_xy` on the field (triple-click to select), `type_text` the number,
press `Tab` to force focus-out, then OK.

**Why:** the AT-SPI write bypasses the VCL modify handler, so the dialog never marks the field
dirty and discards it on commit. Same family of failure as
[[impress-position-size-spinbuttons-ignore-a11y]], but here the read-back LIES, which makes it
easy to believe it worked.
**How to apply:** never trust the read-back on an Options spin button — verify against
`~/.config/libreoffice/4/user/registrymodifications.xcu` or by reopening the dialog.
