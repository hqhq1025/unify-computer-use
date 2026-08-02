---
name: impress-bulk-font-change
description: Bulk font change in Impress needs shape+paragraph+portion AND StyleFamilies, else empty placeholders keep the old font
metadata:
  type: feedback
---

To restyle every text box in an Impress deck over UNO, set `CharFontName`
(+`...Asian`/`...Complex`) at four levels: the shape, its text, each paragraph,
and each text portion. Recurse into `com.sun.star.drawing.GroupShape` and handle
`TableShape` via `shape.Model.getCellByPosition()`.

That alone is NOT enough. Empty placeholders still export
`<a:endParaRPr><a:latin typeface="Arial">` into the pptx even though every live
shape reports the new font, because the value comes from the presentation
styles. Also loop `doc.StyleFamilies` and set the same properties on every style
in every family — that fixes the export and makes newly typed text inherit the
font too.

Bullet fonts are separate and should be LEFT ALONE: they survive as
`<a:buFont typeface="Wingdings|Symbol|Noto Sans Symbols">`. Overwriting them
turns bullet glyphs into letters. Verify by grepping `typeface="..."` in
`ppt/slides/*.xml` of the saved file and checking the only survivors are buFont.

See [[libreoffice-enable-uno-socket-on-running-instance]] for getting the socket
up, and [[libreoffice-uno-strikethrough-impress]] for the same
paragraph-and-portion pattern applied to character effects.
