---
name: libreoffice-uno-strikethrough-impress
description: Strikethrough on Impress text via UNO — set CharStrikeout on the paragraph AND each text portion.
metadata:
  type: reference
---

To strike through specific lines of an Impress outline/body placeholder over UNO:

```python
from com.sun.star.awt.FontStrikeout import SINGLE
shape = slide.getByIndex(1)          # body placeholder; index 0 is usually the title
en = shape.getText().createEnumeration()
paras = [en.nextElement() for _ in iter(lambda: en.hasMoreElements(), False)]
p = paras[0]
p.setPropertyValue("CharStrikeout", SINGLE)
pen = p.createEnumeration()          # also hit each portion — portion-level runs
while pen.hasMoreElements():          # can carry their own CharStrikeout and win
    pen.nextElement().setPropertyValue("CharStrikeout", SINGLE)
```

Setting it only on the paragraph is not reliable when the paragraph has multiple
formatting runs. `doc.store()` writes straight back to .pptx with no format prompt.
Enable the socket first — see [[libreoffice-enable-uno-socket-on-running-instance]].
