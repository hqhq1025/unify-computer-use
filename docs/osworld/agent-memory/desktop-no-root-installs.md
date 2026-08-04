---
name: desktop-no-root-installs
description: This Ubuntu 22.04 desktop has no passwordless sudo; install software via `flatpak --user` instead of snap/apt.
metadata:
  type: project
---

On this machine (Ubuntu 22.04 VM, user `user`), `sudo` always prompts for a password
that I do not have, so `sudo snap install` / `apt install` cannot be completed
unattended. `flatpak --user install flathub <app-id>` works with no elevation.

The root filesystem also runs near-full (~98% on a 49G `/dev/sda3`), and flatpak
enforces a 500MB min-free-space reserve, so a large runtime download fails with
"Not enough disk space". Safe reclaim that worked: `pip cache purge` (freed 2.7G).
Leave `~/.cache/torch`, `~/.cache/huggingface`, `~/.cache/ms-playwright` alone —
those are expensive re-downloads.

The Bash tool's shell also has no `DISPLAY` set; prefix GUI launches with
`DISPLAY=:0` and use `setsid` to detach.

Verified 2026-08-04 while installing Spotify.
