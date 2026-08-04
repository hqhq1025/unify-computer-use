---
name: low-light-ui-preference
description: User runs media/desktop apps in low-light and at night; prefers dark, low-brightness UI accents
metadata:
  type: user
---

The user frequently uses the desktop (VLC in particular) in a low-light environment and at night, and finds bright UI accents straining. They prefer dark / near-black color schemes for UI chrome.

Concretely, they asked for VLC's volume slider to be recolored black-ish; it is now set via `qt-slider-colours` in `~/.config/vlc/vlcrc`. See [[vlc-slider-colour-setting]].

**How to apply:** When offering color or theme choices for desktop apps on this machine, default to the darkest reasonable option rather than stock/bright defaults, and mention the visibility tradeoff when a color would become indistinguishable from a dark background.
