---
name: gnome-settings-segfaults
description: gnome-control-center (GNOME Settings) segfaults on launch in this VM; use CLI tools for system settings instead
metadata:
  type: project
---

On this desktop VM, `gnome-control-center` crashes with a segmentation fault immediately on launch (verified 2026-08-04 with `gnome-control-center sound`), so the Settings GUI cannot be driven for system configuration tasks.

**Why:** Several desktop tasks (audio, display, network) would naturally route through Settings, and the window vanishing mid-task looks like a click failure rather than a crash.

**How to apply:** Reach for the underlying CLI instead — `pactl` / `amixer` for audio, `gsettings` for GNOME prefs, `xdotool key XF86Audio*` for media keys. The shell from Bash shares the user's session (uid 1000) but has no `DISPLAY`, so export `DISPLAY=:0` for X clients.
