---
name: impress-summary-slide-menu-location
description: Impress "Summary Slide" is under the Slide menu (not Insert); it appends a new last slide listing every slide title.
metadata:
  type: reference
---

In LibreOffice Impress 7.x the **Summary Slide** command is `Slide ▸ Summary Slide` — near the bottom, after `Navigate` and before `Expand Slide`. It is NOT in the Insert menu (that's where PowerPoint 2003 put it), so don't waste a probe there.

Behaviour: appends a new slide at the **end** of the deck (not after the current slide), using the "Title, Content" layout, whose outline placeholder lists the title text of every slide that has one. Slides with no title placeholder are silently skipped. The new slide's own title is left empty ("Click to add Title"), and that empty placeholder visually overlaps the bullet list in Normal view — harmless, it doesn't render in the slide show.

Ctrl+S on a .pptx saved straight through with no "Keep current format?" dialog on this box; verify with the file mtime rather than assuming a dialog is waiting. See [[libreoffice-uno-store-blocks-on-dialog]].
