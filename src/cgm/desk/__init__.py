"""The desktop half: the same watch face, in a window instead of in VR.

This is the second frontend the layer split was made for. It needs
Pillow and tkinter and nothing else -- no headset, no SteamVR, no
`openvr` -- so a plain `pip install -e .` is enough to run it.

It imports `cgm.core` and `cgm.face`, and never `cgm.vr`. The traffic
does not go the other way either: the overlay does not know this exists.
"""
