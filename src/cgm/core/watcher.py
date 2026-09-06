"""Picking up edits to config.toml without restarting.

Frontend-neutral: it only compares a file stamp and re-reads the file,
so a desktop window can poll it on a timer the same way the VR draw
loop does.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cgm.core import config as config_mod

# The application's logger, not this module's: these lines are the run
# log the user reads, and they should not change name with the file.
log = logging.getLogger("vrcgm")


class ConfigWatcher:
    """Re-read config.toml whenever it changes on disk.

    Placement is found by trial and error with the headset on, and there
    is no way to judge a change without wearing it. Restarting for every
    nudge means taking the headset off, so the file is watched instead
    and edits land on the next draw.

    A half-written file parses as garbage, and an edit can be plain
    wrong. Neither may take down a resident process, so a bad read is
    logged and the running config is kept.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stamp = self._current_stamp()

    def _current_stamp(self) -> tuple[float, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        # Size as well as mtime: editors can write twice within the
        # timestamp resolution of the filesystem.
        return (st.st_mtime, st.st_size)

    def poll(self) -> config_mod.Config | None:
        """Return the reloaded config once the file changes, else None."""
        stamp = self._current_stamp()
        if stamp is None or stamp == self._stamp:
            return None
        self._stamp = stamp

        try:
            return config_mod.load(self._path)
        except (OSError, ValueError) as exc:
            # TOMLDecodeError is a ValueError, so a partial write lands here.
            log.warning("ignoring the edited config: %s", exc)
            return None
