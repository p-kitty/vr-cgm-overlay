"""The SteamVR half. Almost everything here needs `openvr` installed.

Kept behind the `vr` extra so a desktop-only install does not pull
SteamVR bindings it would never load.

`cgm.vr.session` is the exception, and deliberately: it is the thread
that owns an overlay's lifetime, and it is handed one rather than
making its own, so it imports nothing from SteamVR and can be driven
by a stand-in with no headset in the room.
"""
