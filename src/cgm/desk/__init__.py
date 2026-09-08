"""The desktop half: the same watch face, in a window instead of in VR.

This is the second frontend the layer split was made for. It needs
Pillow and tkinter and nothing else -- no headset, no SteamVR, no
`openvr` -- so a plain `pip install -e .` is enough to run it.

It is also where the settings window lives, for the reason it is the
only frontend that can have one: a dialog needs a desktop, and the
overlay has a controller. `cgm.desk.settings` writes `config.toml` and
nothing else, so what it changes reaches the overlay the same way a text
editor's change does.

It imports `cgm.core` and `cgm.face`, and never `cgm.vr`. The traffic
does not go the other way either: the overlay does not know this exists.
"""
