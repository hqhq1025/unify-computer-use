---
name: impress-uno-blank-slide-with-image
description: Building truly blank Impress slides holding one full-bleed image, over UNO.
metadata:
  type: reference
---

Recipe for "N blank slides, one image each" in Impress over UNO (see
[[libreoffice-enable-uno-socket-on-running-instance]] to get the socket):

- `pages.insertNewByIndex(pages.getCount())` to grow the deck.
- Per page: set `page.Layout = 20` (blank) **and** then loop
  `while page.getCount(): page.remove(page.getByIndex(0))`. Do both — the layout
  change alone is not a reliable way to guarantee zero placeholder/text shapes,
  and the removal loop is what makes the exported XML contain `<p:pic>` with no
  `<p:sp>`/`<p:txBody>`.
- Image: `doc.createInstance("com.sun.star.drawing.GraphicObjectShape")`, `page.add(shape)`
  **before** setting `shape.GraphicURL = uno.systemPathToFileUrl(path)`, then
  `setSize(Size(w,h))` / `setPosition(Point(x,y))` in 1/100 mm.
  Page.Width/Height are in the same unit (Screen 16:9 = 28002 x 15752).
- Save with `storeAsURL` + FilterName `Impress MS PowerPoint 2007 XML`, not
  `storeToURL`, when the target path is the document's own path — storeAsURL
  leaves `isModified()` False so the GUI has no stale unsaved state.
- Verify by unzipping the .pptx: count `<p:pic>` / `<p:sp>` / `<p:txBody>` per
  slide and md5 `ppt/media/*` against the source files to prove image order.
