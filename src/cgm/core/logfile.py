"""Keep the run log in a file, and put every traceback in it.

Most of what `NOTES.md` still has open is answered by the log rather
than by the face: how far `graphData` lags, whether the arrow bends or
fits, whether a token expiry recovers, whether the fetch schedule holds
across an evening. Each of those is a `--window` left running and read
afterwards -- and a console log is gone the moment the console is, so
"afterwards" used to mean scrolling back through a terminal that had to
still be open. So is the traceback of whatever went wrong overnight,
which is the one line worth having when something did.

A file a day, two weeks of them. A fetch a minute is about 200 KB a
day, so two weeks costs a few megabytes and holds long enough to notice
something, go and look, and still find it there.

Only the resident run keeps one. `--dry-run` prints what it found and
exits, and it is often run while the resident process is up, which
would be two processes appending to one file and one of them renaming
it out from under the other at midnight.

This is not Tk and not VR, so it lives here, the way `cgm.core.console`
does.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

log = logging.getLogger("vrcgm")

LOG_NAME = "vr-cgm-overlay.log"

# How many days of log are kept besides today's.
LOG_DAYS = 14

# One line format for the console and the file alike, so a line copied
# out of either reads the same.
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# The file puts the date in front, where the console shows only the time:
# a console is read as it scrolls, and a file is read days later, often
# searched across several at once.
FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"


def log_to_file(log_dir: Path) -> logging.Handler | None:
    """Add a daily rotating file to the root logger. None if it cannot.

    UTF-8 by name, for the same reason `cgm.core.console` exists: the
    fetch lines carry the diagonal arrows, which the locale encoding
    here has no room for, and `logging` does not raise over that -- it
    drops the line, which is exactly the line worth keeping.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / LOG_NAME,
            when="midnight",
            backupCount=LOG_DAYS,
            encoding="utf-8",
        )
    except OSError as exc:
        # A log we cannot write is not worth refusing to start over. The
        # console still has it.
        log.warning("not keeping a log file in %s: %s", log_dir, exc)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT, FILE_DATEFMT))
    logging.getLogger().addHandler(handler)
    log.info("logging to %s", log_dir / LOG_NAME)
    return handler


def log_uncaught() -> None:
    """Send a traceback nothing caught through the log, not only stderr.

    Python prints one straight to stderr, past every handler, so without
    this the file holds everything except the reason the process died.
    Threads are covered separately because they have their own hook, and
    a thread dying is exactly the failure that leaves the rest of the
    process running as if nothing happened.

    Ctrl-C is left to the default: it is how the process is stopped, not
    something that went wrong.
    """
    def excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "a thread"
        log.critical(
            "uncaught exception in %s",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
