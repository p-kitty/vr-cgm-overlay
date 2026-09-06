"""The frontend-neutral half: fetching, config, and the schedules.

Nothing in here needs a headset, so all of it runs under a desktop
window or a bare test as readily as under the VR draw loop.

`cgm.core.poller` does reach into `cgm.face` for `TrendTuning`, because
the fetch log and the face have to name the same trend source or the log
will claim one the face is not drawing. That is the only crossing, and it
goes downwards -- `cgm.face` imports nothing from here.
"""
