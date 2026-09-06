"""Blood glucose from LibreLinkUp, on a watch face.

Two shared layers and two frontends, split by what each one needs to
run:

  - `cgm.core` — the API client, the config, the fetch schedule and the
    config watcher. Nothing here needs a headset or a screen.
  - `cgm.face` — the watch face drawing. Needs Pillow and a font, and
    returns an image; it does not know where the image ends up.
  - `cgm.vr` — the SteamVR overlay and its tuning aid. Needs `openvr`,
    which is why it is an optional install (`pip install -e .[vr]`).
  - `cgm.desk` — the same face in a desktop window. Needs tkinter and
    nothing else, so a plain `pip install -e .` can run it.

The split exists so the two frontends can show the same face without
either one importing the other's dependencies, and neither imports the
other. `cgm.main` wires whichever one was asked for.
"""
