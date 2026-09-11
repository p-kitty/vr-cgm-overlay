"""One running copy per config file.

A second copy is easy to start by accident once the first one starts
with Windows: sign in, forget, and double-click it again. Each copy
brings its own `Poller`, so the account is polled twice as often --
the thing `polling.interval_sec` has a floor to prevent -- and its own
`LowAlert`, so a low announces itself twice. `cgm.main` exists so that
one process owns both, and this is what keeps it to one process.

Per config file rather than per machine, because a config is an
account: following two people is two configs and legitimately two
copies, while the same config twice is always a mistake.

A Windows named mutex rather than a lock file. The kernel drops it when
the process ends, however it ends, so a crash or a forced shutdown
cannot leave a stale lock behind that refuses the next start.

`--dry-run` does not take it. It is one fetch, and running it beside the
resident process is an ordinary way to check the API.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
from pathlib import Path

log = logging.getLogger("vrcgm")

ERROR_ALREADY_EXISTS = 183

try:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except (AttributeError, OSError):  # not Windows
    _kernel32 = None
else:
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def lock_name(config_path: Path) -> str:
    """The mutex name for one config file.

    Hashed, because a path can be longer than a mutex name may be and
    carries backslashes, which the kernel reads as a namespace. Lower
    case first, since Windows paths are not case sensitive and the same
    file must not get two names. `Local\\` keeps it to this sign-in, so
    two people on one machine do not refuse each other.
    """
    resolved = str(config_path.resolve()).lower()
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return f"Local\\vr-cgm-overlay-{digest}"


class InstanceLock:
    """Held for as long as this copy runs. Build it with `claim`."""

    def __init__(self, handle) -> None:
        self._handle = handle

    def release(self) -> None:
        """Give the name back. Process exit does the same."""
        if self._handle and _kernel32 is not None:
            _kernel32.CloseHandle(self._handle)
        self._handle = None


def claim(config_path: Path) -> InstanceLock | None:
    """Take the lock for `config_path`. None if another copy holds it.

    Anything short of a clear "already running" lets this copy run: a
    lock that cannot be made is not a reason to show no glucose at all.
    """
    if _kernel32 is None:
        return InstanceLock(None)
    handle = _kernel32.CreateMutexW(None, False, lock_name(config_path))
    error = ctypes.get_last_error()
    if not handle:
        log.warning("cannot tell whether another copy is running (error %d)", error)
        return InstanceLock(None)
    if error == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return InstanceLock(handle)
