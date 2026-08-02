---
name: verify-render-via-pdf-export
description: When the OCU a11y bridge times out on soffice, verify rendering by exporting to PDF over UNO and running pdftotext.
metadata:
  type: feedback
---

After soffice is relaunched following a crash, the OCU accessibility bridge often stops
answering — `get_app_state`/`get_screenshot` time out at 30s even though the process and
window are alive (`wmctrl -l` shows the title). Do not conclude the app is broken.

**Why:** the bridge is a separate channel from UNO; UNO keeps working when a11y does not,
so there is still a way to see what the document actually looks like.

**How to apply:** `storeToURL("file:///tmp/check.pdf", FilterName="impress_pdf_Export")`
(or `writer_pdf_Export` / `calc_pdf_Export`), then `pdftotext -layout`. The extracted text
shows rendered bullet glyphs, indents and line breaks, which is real render evidence rather
than just the UNO property values. The export dispatch can outlast a 2-minute Bash timeout
while still finishing — re-check the output path before retrying.

Related: [[libreoffice-uno-soffice-crash-recovery]], [[libreoffice-crash-recovery-dialog]],
[[ocu-tools-down-use-pyatspi]].
