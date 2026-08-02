---
name: pptx-remove-bullet-keep-indent
description: Removing a bullet from one pptx paragraph also needs indent="0", or the text jumps left into the hanging-indent gutter.
metadata:
  type: reference
---

In OOXML, a bulleted paragraph is typically `<a:pPr marL="457200" indent="-431640">` — `marL` is where the text sits and the negative `indent` pulls the bullet back into the gutter. Dropping the bullet (replace `<a:buClr>…</a:buClr><a:buFont/><a:buChar/>` with `<a:buNone/>`) is only half the job: the negative `indent` still applies to the first line, so the text slides left and no longer lines up with the bulleted siblings above it. Set `indent="0"` and keep `marL` unchanged to keep the text edge aligned.

Patching `ppt/slides/slideN.xml` in the zip is more precise here than the GUI, since Format > Bullets and Numbering does not expose the two values independently. See [[xlsx-patch-zip-directly]] for the zip-rewrite pattern; reloading afterwards can kill soffice, see [[libreoffice-uno-soffice-crash-recovery]].
