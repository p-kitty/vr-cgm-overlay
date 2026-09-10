"""The frontend-neutral half: fetching, config, and the schedules.

Nothing in here needs a headset, so all of it runs under a desktop
window or a bare test as readily as with the overlay up.

`cgm.core` does reach into `cgm.face`, and only ever so that one number
is not written down twice: `cgm.core.poller` takes `TrendTuning`, so the
fetch log cannot name a trend source the face is not drawing, and
`cgm.core.config` takes the graph's floor and time step, so a config is
rejected against the axis it will actually be drawn on. The crossing
goes one way only -- `cgm.face` imports nothing from here.
"""
