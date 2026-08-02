---
name: impress-background-all-slides
description: Impress Slide Properties sets the background of the CURRENT slide only; use UNO to hit every page.
metadata:
  type: reference
---

Slide → Slide Properties… → Background tab is the GUI entrance for a slide
background in Impress, but in LO 7.3 it applies to the **current slide only** —
there is no "apply to all pages?" confirmation prompt. Multi-selecting in Slide
Sorter is unreliable too: after clicking the "Slide Sorter" view tab, focus stays
on the *tab* rather than the sorter canvas, so Ctrl+A is silently ignored.

Reliable way to set every slide's background (see [[libreoffice-enable-uno-socket-on-running-instance]]):

```python
from com.sun.star.drawing.FillStyle import SOLID
pages = doc.getDrawPages()
for i in range(pages.getCount()):
    bg = doc.createInstance("com.sun.star.drawing.Background")
    bg.FillStyle = SOLID
    bg.FillColor = 0x2A6099          # LibreOffice standard-palette "Blue"
    pages.getByIndex(i).Background = bg   # must assign; mutating in place is a no-op
```

The service name is `drawing.Background`. `drawing.FillProperties` looks like the right
one and is not: it throws `ServiceNotRegisteredException: unknown service`.

Also note: in the Background tab, clicking a palette swatch by element_index only
raises its tooltip. Write the hex into the "New" panel's Hex field with
`set_value` instead — the R/G/B spin buttons update to confirm it parsed.

pptx round-trips fine: `doc.store()` writes `<p:bg>` with the srgbClr into each
`ppt/slides/slideN.xml`, and no Keep Format dialog appears. Grep for it case-insensitively:
the hex is written **lowercase** (`<a:srgbClr val="2a6099"/>`), so an uppercase
grep reports 0 hits on a file that is actually correct.
