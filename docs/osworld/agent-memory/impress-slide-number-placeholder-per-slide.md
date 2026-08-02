---
name: impress-slide-number-placeholder-per-slide
description: Recoloring Impress slide numbers means walking every DrawPage plus every MasterPage, not just the masters.
metadata:
  type: reference
---

To restyle the slide number in Impress over UNO, filter shapes by
`shape.getShapeType() == "com.sun.star.presentation.SlideNumberShape"` and iterate
**both** `doc.getDrawPages()` and `doc.getMasterPages()`.

**Why:** the intuition "placeholders live on the master, edit it once" is wrong for
pptx decks exported from Google Slides — each slide carries its own copy of the
SlideNumberShape, so a master-only edit changes nothing visible. One deck had 15
per-slide shapes plus 4 masters = 19 shapes to touch.

**How to apply:** set `CharColor` on the shape, on each paragraph, and on each text
portion (same three-level rule as [[libreoffice-uno-strikethrough-impress]]), then
`doc.store()` — it keeps the pptx filter and does not raise the keep-format dialog
(cf. [[libreoffice-uno-store-blocks-on-dialog]]). Verify in the saved zip with a
**case-insensitive** match: LibreOffice writes `<a:srgbClr val="ff0000"/>` lowercase,
so grepping for `FF0000` reports a false failure.
