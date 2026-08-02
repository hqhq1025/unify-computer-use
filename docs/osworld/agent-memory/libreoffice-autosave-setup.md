---
name: libreoffice-autosave-setup
description: Real auto-save in LibreOffice needs UserAutoSaveEnabled set over UNO; the Options dialog only offers AutoRecovery.
metadata:
  type: reference
---

Tools > Options > Load/Save > General has "Save AutoRecovery information every: N minutes",
but in this 7.3 build there is NO "Automatically save the document too" checkbox — so the
dialog alone only writes crash-recovery data, and the user still has to press Ctrl+S.

The real auto-save flag exists in the schema but is unexposed. Set it over UNO
(see [[libreoffice-enable-uno-socket-on-running-instance]]) at nodepath
`/org.openoffice.Office.Recovery/AutoSave`:

- `Enabled` (bool)            — AutoRecovery on
- `TimeIntervall` (int)       — minutes; note the double-L spelling
- `UserAutoSaveEnabled` (bool)— THE one that actually saves the document

Use `com.sun.star.configuration.ConfigurationUpdateAccess` + `commitChanges()`; it lands in
`~/.config/libreoffice/4/user/registrymodifications.xcu` immediately.

**Why:** the checkbox users expect ("auto-save so I don't hit Ctrl+S") is absent from the GUI here,
so a dialog-only change silently under-delivers on the request.
**How to apply:** flip the checkbox in the dialog for visible confirmation, then set
`UserAutoSaveEnabled` over UNO, then verify all three keys in registrymodifications.xcu.
