"""Stand in for openvr where it is not installed.

openvr is an optional extra (`pip install -e .[vr]`), and the checks
that import `cgm.vr.overlay` for its arithmetic -- the orbit geometry,
the gaze fade -- need nothing from it but the matrix type its helpers
return. Installing this stub first keeps them runnable on a core-only
install, or with no SteamVR at all. Where the real bindings are there,
it does nothing.
"""

from __future__ import annotations

import sys
import types


class _HmdMatrix34_t:
    def __init__(self) -> None:
        self.m = [[0.0] * 4 for _ in range(3)]


def install() -> None:
    """Make `import openvr` succeed, with the real module if there is one."""
    try:
        import openvr  # noqa: F401
    except ModuleNotFoundError:
        stub = types.ModuleType("openvr")
        stub.HmdMatrix34_t = _HmdMatrix34_t  # type: ignore[attr-defined]
        sys.modules["openvr"] = stub
