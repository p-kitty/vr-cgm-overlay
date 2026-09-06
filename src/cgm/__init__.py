"""Blood glucose from LibreLinkUp, on a watch face.

Three layers, split by what each one needs to run:

  - `cgm.core` — the API client, the config, the fetch schedule and the
    config watcher. Nothing here needs a headset or a screen.
  - `cgm.face` — the watch face drawing. Needs Pillow and a font, and
    returns an image; it does not know where the image ends up.
  - `cgm.vr` — the SteamVR overlay and its tuning aid. Needs `openvr`,
    which is why it is an optional install (`pip install -e .[vr]`).

The split exists so a second frontend can show the same face outside VR
without either one importing the other's dependencies.
"""
