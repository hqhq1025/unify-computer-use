---
name: impress-insert-audio-embed
description: Insert > Audio or Video... in Impress — untick Link to embed, which survives .pptx export as ppt/media/*.mp3.
metadata:
  type: reference
---

To put an audio file into an Impress deck: **Insert > Audio or Video...**, then in the
file dialog **untick the "Link" check box** (it is checked by default) before typing the
path into the File name field and pressing Open.

- Link ticked → the .pptx only references the file on disk; moving the deck breaks it.
- Link unticked → the media is copied into the document package. Confirm with
  `shape.MediaURL`, which becomes `vnd.sun.star.Package:Media/<name>.mp3`.

LibreOffice 7.3 exports this correctly to .pptx: the bytes land in `ppt/media/media4.mp3`
and `ppt/slides/_rels/slide1.xml.rels` gets both the OOXML
`.../relationships/audio` and the Microsoft `.../2007/relationships/media` entries, so
PowerPoint plays it. Verify with `unzip -l` plus a grep of the slide rels.

The shape drops in centred at 5x5 cm and usually covers the title — reposition it, but
note the sidebar/F4 fields will not do that: see
[[impress-position-size-spinbuttons-ignore-a11y]].
