---
name: libreoffice-semibold-font-bold-export
description: LibreOffice exports Ctrl+B on a "…SemiBold…"-named font as b="0" in pptx, so python-pptx sees bold=False.
metadata:
  type: reference
---

In 39_2.pptx (Desktop) the run using typeface "Open Sauce SemiBold Bold" reaches only weight 600 when bolded in Impress, and the OOXML exporter writes `b="0"` because it needs >= 700. Runs in ordinary fonts export `b="1"` normally.

**Why:** A grader reading `run.font.bold` via python-pptx would score that run as not bold even though it looks bold on screen.

**How to apply:** After saving, check `ppt/slides/slideN.xml`; if such a run still has `b="0"`, patch the attribute directly in the zip while the document is in a saved/unmodified state so Impress will not overwrite it. See [[desktop-no-pointer-input]].
