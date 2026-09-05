"""Unit tests for the parts that need neither a headset nor the network.

The watch face is judged by eye with `tools/preview.py`, and the orbit
geometry is asserted by `tools/check_orbit.py`. What is left over is the
plain logic those two cannot reach: timestamp parsing, the fetch
schedule, config validation, and the colour thresholds.

Nothing here touches `src/overlay.py`, which needs SteamVR, or the HTTP
side of `src/librelink.py`, where a mock would only prove the mock
matches itself. The real risk there is the unofficial API changing shape,
and that only `python src/main.py --dry-run` can see.

`src` is not an installed package, so it goes on the path once, here,
instead of in every test module -- the same thing the two tools each do
for themselves.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
