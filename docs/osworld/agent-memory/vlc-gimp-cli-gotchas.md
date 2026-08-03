---
name: vlc-gimp-cli-gotchas
description: Two non-obvious failure modes when scripting VLC frame extraction and GIMP batch script-fu on this desktop
metadata:
  type: reference
---

On this Linux desktop, headless VLC + GIMP media pipelines hit two silent traps:

1. `vlc -I dummy --video-filter=scene --vout=dummy` logs "Failed to create video converter"
   and writes PNGs at the **wrong height** (640x386 for a 640x360 source). Passing
   `--scene-width` / `--scene-height` explicitly forces a working scaler and correct output.
2. GIMP 2.10 batch (`gimp -i -b`) **hangs indefinitely at ~0% CPU** — no error — if the
   script-fu code calls `sort`, `list-head`, or `file-glob`. Generate an explicit literal
   file list into the .scm instead. Also, definitions from a `load` in one `-b` are not
   visible to a later `-b`; wrap load + call in a single `(begin ...)`.

**Why:** both fail without a diagnostic, so they look like the whole approach is broken.
**How to apply:** when scripting these tools, set scene dimensions explicitly and keep
script-fu to plain builtins; smoke-test GIMP batch on 2-3 inputs before the full run.
