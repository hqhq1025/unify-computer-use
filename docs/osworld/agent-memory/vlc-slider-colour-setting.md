---
name: vlc-slider-colour-setting
description: Where VLC's volume-slider color lives and how to reach it (Qt advanced prefs, qt-slider-colours)
metadata:
  type: reference
---

VLC's volume slider color is the `qt-slider-colours` key in `~/.config/vlc/vlcrc` — twelve `;`-separated 0-255 numbers forming four RGB stops of the slider's gradient.

GUI path: Tools > Preferences > "All" radio (bottom left) > Interface > Main interfaces > Qt > "Define the colors of the volume slider".

Gotchas found on this machine (VLC 3.0.16):
- VLC only reads this at startup, so it must be restarted for a change to show.
- The bash tool has no `DISPLAY` set; launch GUI apps with `DISPLAY=:0` or VLC exits with "no suitable interface module".
- In the prefs dialog, AT-SPI clicks on the Simple/All radios and on the category tree rows select the wrong row or don't fire; pixel clicks work.

Related: [[low-light-ui-preference]].
