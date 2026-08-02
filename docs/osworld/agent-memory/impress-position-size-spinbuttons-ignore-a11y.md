---
name: impress-position-size-spinbuttons-ignore-a11y
description: set_value on LibreOffice Position/Size spin buttons reports success but never reaches the document; move shapes over UNO instead.
metadata:
  type: reference
---

Writing a shape's geometry through AT-SPI `set_value` does not work in LibreOffice,
in either place the fields appear:

- Sidebar **Properties > Position and Size** — the control shows the new number, but
  nothing commits (no pixel change, status bar still shows the old `X / Y  W x H`).
- The modal **F4 Position and Size** dialog — same. The spin button reads back the new
  value, yet clicking **OK** applies the *original* geometry, because the dialog reads
  its own field state and the a11y write never updated it.

Both report `[a11y][semantic] Wrote the value ... and read it back to confirm`, which
confirms only the control, not the document. Always re-check the status bar.

Use UNO instead (units are 1/100 mm):

```python
from com.sun.star.awt import Point, Size
shape.setSize(Size(3000, 3000))        # 3 cm x 3 cm
shape.setPosition(Point(1200, 14800))
```

Enable the socket first if needed — see [[libreoffice-enable-uno-socket-on-running-instance]].
Same family of failure as [[libreoffice-uno-range-sort-noop]]: the API accepts the call
and silently does nothing. Related: [[libreoffice-uno-fallback]].
