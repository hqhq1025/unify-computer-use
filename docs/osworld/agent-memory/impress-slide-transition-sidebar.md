---
name: impress-slide-transition-sidebar
description: Apply a per-slide transition in Impress via the Slide Transition sidebar deck, and verify it by reading p:transition out of the saved pptx.
metadata:
  type: reference
---

Per-slide transitions in Impress live only in the sidebar — there is no Slide-menu entry
(unlike [[impress-summary-slide-menu-location]]). Recipe:

1. Select the slide in the Slides panel (`panel "Slide N"`).
2. Click the sidebar deck `tool bar "Slide Transition"` toggle button. The deck expands into
   a `list` of `list item "Dissolve"`, `"Fade"`, `"Wipe"`, … — click the one you want.
   Clicking the list item applies immediately; there is no OK button.
   Do NOT click "Apply Transition to All Slides" unless every slide is meant to change.
3. Confirmation that it landed: the slide's thumbnail in the Slides panel gains a small
   star/transition marker, and the list item shows `[selected]`.

Verify in the saved file rather than trusting the tree — unzip the pptx and read the slide:

```bash
python3 -c "
import zipfile,re
d=zipfile.ZipFile('FILE.pptx').read('ppt/slides/slide1.xml').decode('utf8','replace')
print(re.search(r'<mc:AlternateContent.*?</mc:AlternateContent>',d,re.S).group(0))"
```

Dissolve round-trips as `<p:transition spd="slow" p14:dur="2000"><p:dissolve/></p:transition>`
inside an `mc:AlternateContent` Choice/Fallback pair.

Ctrl+S on a .pptx here saves in PowerPoint format with **no** "Use PowerPoint Format!" prompt —
the alien-format warning is off in this profile, so nothing blocks the way it does in
[[libreoffice-uno-store-blocks-on-dialog]]. Confirm the save by comparing the file mtime to
`date`, since the window looks pixel-identical before and after.
