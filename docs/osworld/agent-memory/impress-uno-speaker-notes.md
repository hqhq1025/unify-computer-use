---
name: impress-uno-speaker-notes
description: Set an Impress slide's speaker notes over UNO via DrawPage.NotesPage shape index 1.
metadata:
  type: reference
---

Speaker notes for slide N: `doc.getDrawPages().getByIndex(N).NotesPage`. That page holds
exactly 2 shapes — index 0 is a `presentation.PageShape` (the slide thumbnail, whose
`getString()` throws) and **index 1 is the `presentation.NotesShape`**. Call
`setString(text)` on index 1. Neither shape reports `supportsService(
"com.sun.star.presentation.NotesShape")` as True, so match on `getShapeType()` instead.

On pptx save the notes land in `ppt/notesSlides/notesSlideN.xml`, numbered by SLIDE index —
a note on slide 2 only produces `notesSlide2.xml`, with no `notesSlide1.xml`. Don't read a
missing `notesSlide1.xml` as a failed save.

Reach the socket first via [[libreoffice-enable-uno-socket-on-running-instance]]; suppress the
Keep-format prompt per [[libreoffice-uno-store-blocks-on-dialog]]; bold the title by setting
CharWeight on shape + paragraphs + portions as in [[libreoffice-uno-strikethrough-impress]].
