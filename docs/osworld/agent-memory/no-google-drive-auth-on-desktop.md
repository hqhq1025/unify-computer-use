---
name: no-google-drive-auth-on-desktop
description: This Linux desktop has no usable Google Drive authentication path; Drive uploads need credentials from the user.
metadata:
  type: project
---

As of 2026-08-03, nothing on this desktop can reach Google Drive without the user supplying credentials:

- Chrome (`~/.config/google-chrome`, profile "Default") has no Google session — `drive.google.com` redirects to the sign-in page, and the cookie DB holds only `NID`/`OTZ`/`__Host-GAPS`. No saved logins.
- No `rclone` or `gdrive` binary, no `~/.config/rclone`.
- `~/OSWorld/evaluation_examples/settings/googledrive/settings.yml` points at `client_secrets.json` and `credentials.json`, but neither file exists — only `settings.yml` is in that directory.
- GNOME Online Accounts (`~/.config/goa-1.0/`) is empty and no gvfs mounts exist.

**Why:** Tasks that end in "upload to Google Drive" will get all the way to the upload and then stall, so it is worth checking this up front.

**How to apply:** Do the local work first (extract/stage files), then ask the user to sign in to Drive in Chrome or to drop credentials in the googledrive settings dir. Do not guess at account names or passwords.
