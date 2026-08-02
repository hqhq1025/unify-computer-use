---
name: impress-read-char-props-from-portion
description: Reading Impress text formatting over UNO — shape- and paragraph-level CharHeight/CharColor lie; read the text portion.
metadata:
  type: reference
---

When a task says "same color / font size as the other title", do NOT read
`shape.CharHeight` / `shape.CharColor`. On a .pptx title placeholder those report
the placeholder's *default* (seen: 18pt, 0x000000, Arial) while the visible text
was 44pt, 0xFF0000, Calibri. The paragraph reported the same wrong defaults. Only
the text portion had the truth:

```python
para = next(iter_enum(shape.createEnumeration()))
port = next(iter_enum(para.createEnumeration()))
port.CharHeight, hex(port.CharColor & 0xFFFFFF), port.CharFontName   # <- real values
```

Alignment is the exception: `ParaAdjust` is only on the paragraph (3 = CENTER).
Write the values back at all three levels — see [[libreoffice-uno-strikethrough-impress]].

**Why:** copying from the shape silently produces a title that looks nothing like
the source, and the a11y tree shows no font info to catch it.
**How to apply:** inspect the portion before copying formatting; confirm against
the saved XML (`a:rPr sz=` / `a:srgbClr val=`) rather than the shape props.
Related: [[libreoffice-enable-uno-socket-on-running-instance]].
