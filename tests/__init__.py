"""Unit tests for the parts that need neither a headset nor the network.

The watch face is judged by eye with `tools/preview.py`; the orbit
geometry and the gaze fade are asserted by `tools/check_orbit.py` and
`tools/check_gaze.py`. What is left over is the plain logic those cannot
reach: timestamp parsing, the trend fit, the fetch schedule, config
validation, and the colour thresholds.

Nothing here touches the SteamVR bindings, or the HTTP side of
`cgm.core.librelink`, where a mock would only prove the mock matches
itself. The real risk there is the unofficial API changing shape, and
that only `vr-cgm-overlay --dry-run` can see.

`cgm.vr.session` is tested all the same. It is the thread the overlay
lives on rather than the overlay, it is handed one instead of making
its own, and a stand-in is enough to say whether it starts, stops,
reopens and draws when it should.

    python -m unittest discover -s tests
"""
