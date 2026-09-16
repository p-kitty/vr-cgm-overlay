"""The mean of the history on hand.

Like the sparkline, this costs no request and keeps nothing: it is a
third reading of the twelve hours or so of `graphData` that
`Reading.history` already carries. So it is an average of that and no
longer -- one meal moves it, and it rises and falls again inside a day.
It is labelled with the span it covers every time it is drawn for that
reason: twelve hours is what the API usually sends, not a promise.

The mean is taken over the samples as they are, not weighted by time.
`graphData` is one point every fifteen minutes, so the samples already
are the time weighting, and a stretch the sensor did not scan has no
points in it rather than an interpolated line pulling the mean towards
whatever was either side.
"""

from __future__ import annotations

from typing import NamedTuple

# The least history an average is drawn from. A fresh sensor or a
# restart after a long gap can hand over a handful of points spanning
# minutes, and a "mean" of those is the current reading wearing a label
# that says otherwise. An hour is four samples, which is the least that
# averages anything.
MIN_SPAN_MIN = 60.0


class Average(NamedTuple):
    """The mean of a history, and how much history it is the mean of."""

    mgdl: float
    span_min: float
    count: int


def history_average(points) -> Average | None:
    """The mean of `points`, or None when they span under MIN_SPAN_MIN.

    `points` is anything with `.at` and `.mgdl`, oldest first -- a
    `Reading.history`.
    """
    if len(points) < 2:
        return None
    span = (points[-1].at - points[0].at).total_seconds() / 60.0
    if span < MIN_SPAN_MIN:
        return None
    mean = sum(p.mgdl for p in points) / len(points)
    return Average(mean, span, len(points))
