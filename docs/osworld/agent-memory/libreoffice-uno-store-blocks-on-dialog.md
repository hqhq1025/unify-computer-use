---
name: libreoffice-uno-store-blocks-on-dialog
description: doc.store() over UNO hangs forever when LibreOffice raises a modal save dialog; WarnAlienFormat=False prevents the Keep-format one.
metadata:
  type: reference
---

`doc.store()` over the UNO bridge blocks indefinitely if LibreOffice puts up a modal dialog —
commonly "Document Has Been Changed by Others" (the file's mtime advanced after it was opened)
or "Keep current format". The Bash call just times out with no error explaining why.

**Prevent** the "Keep current format" one outright — set it in the same script, before `store()`,
instead of waiting for the hang and clicking it away:

```python
from com.sun.star.beans import PropertyValue
cp = smgr.createInstanceWithContext("com.sun.star.configuration.ConfigurationProvider", ctx)
arg = PropertyValue(); arg.Name = "nodepath"
arg.Value = "/org.openoffice.Office.Common/Save/Document"
node = cp.createInstanceWithArguments(
    "com.sun.star.configuration.ConfigurationUpdateAccess", (arg,))
node.setPropertyValue("WarnAlienFormat", False)
node.commitChanges()
doc.store()          # returns immediately on a .pptx/.xlsx; check doc.isModified() == False
```

The `ConfigurationProvider` step is not optional boilerplate: asking the ServiceManager for
`ConfigurationUpdateAccess` directly returns **None** with no error, and the failure only
surfaces one line later as `'NoneType' object has no attribute 'setPropertyValue'`.

If a dialog does appear: call `get_app_state` to see it, dismiss it with the OCU `click` tool,
then re-run the script. Wrap UNO scripts in `timeout 90` so a hang doesn't eat the tool budget.

The dialog can be parked OFF-SCREEN (seen at y=1240 on a 1080-tall display) and `wmctrl -e`
will not move it. Don't chase it with pixels — `find`/`click` address it through the
accessibility tree regardless of where it sits, and `find` flags it as a modal dialog.
Verify the on-disk mtime/content afterwards rather than trusting the click.

The store runs inside soffice, not the client: dismissing the dialog completes the save even
after `timeout` already killed the python process. Check the file on disk before re-running.

Related: [[libreoffice-uno-fallback]], [[libreoffice-uno-formula-separator]],
[[libreoffice-uno-changed-by-others-dialog]]
