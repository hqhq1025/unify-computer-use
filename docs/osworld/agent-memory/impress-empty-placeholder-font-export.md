---
name: impress-empty-placeholder-font-export
description: Empty Impress placeholders export endParaRPr with the layout's default font, ignoring the CharFontName you set over UNO.
metadata:
  type: project
---

When changing the font of a slide, an EMPTY placeholder ("Click to add Text") writes
`<a:endParaRPr><a:latin typeface="Arial"/>` on pptx save no matter what you set — shape
property, paragraph, or a full-range text cursor. The cursor read-back even reports the new
font correctly, so the write looks like it landed.

**Why:** the export path emits the layout/master default for a placeholder that has no runs.

**How to apply:** set the font over UNO as usual (see [[impress-bulk-font-change]]), `store()`,
then patch the leftover `typeface="<old>"` in `ppt/slides/slideN.xml` with the zip-rewrite
recipe in [[xlsx-patch-zip-directly]]. Do not save from soffice afterwards or the patch is
undone. Non-empty shapes are fine and need no patching.
