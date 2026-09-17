"""Open the first-run sign-in window and fill it in, the way a user does.

`tests/test_firstrun.py` covers every decision under the window without
starting Tk. What only exists once Tk is running is checked here: that
the boxes are attached to what gets sent, that a refusal is said on the
window and the button comes back, that Return submits, that a sign-in
that worked writes the file and closes the window -- and that the window
keeps repainting while the sign-in is out, which is the reason it runs on
a thread.

The LibreLinkUp client is stood in for, so this needs no network and no
account, and the config is a throwaway file in a temporary directory.
**Nothing here touches your own config.toml.**

    python tools/check_signin.py           assert and exit
    python tools/check_signin.py --show    leave it open to look at

With `--show`, a password of `wrong` is refused and anything else is
accepted after a second, so both halves can be seen.

Needs a desktop -- it is a window -- but no headset and no network.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

from cgm.core import config as config_mod
from cgm.core import paths
from cgm.desk.signin import SignInWindow

REFUSED = "LibreLinkUp did not accept that sign-in."

# Long enough that the loop turns over several times while it waits.
ATTEMPT_SEC = 0.5


def say(what: str, detail) -> None:
    print(f"  {what:32} {detail}")


def stand_in(seen: list, released: threading.Event | None = None):
    """A try_account that refuses the password `wrong` and takes the rest."""

    def try_account(email: str, password: str) -> str | None:
        seen.append((email, password))
        if released is not None:
            released.wait(5)
        else:
            time.sleep(ATTEMPT_SEC)
        return REFUSED if password == "wrong" else None

    return try_account


def wait_for(window: SignInWindow, done, timeout: float = 5.0) -> None:
    """Turn Tk's loop over until `done()` holds, or fail."""
    end = time.monotonic() + timeout
    while not done():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting on the sign-in window")
        window._root.update()
        time.sleep(0.02)


def check(directory: Path) -> None:
    path = directory / "vr-cgm-overlay" / "config.toml"
    seen: list[tuple[str, str]] = []
    released = threading.Event()
    window = SignInWindow(
        path, example=paths.example_config(), try_account=stand_in(seen, released)
    )
    root = window._root
    root.update()
    assert root.winfo_viewable(), "the window never appeared"
    say("opened", root.title())

    # A refusal: said on the window, nothing written, the button back.
    window.email.set("someone@example.org")
    window.password.set("wrong")
    window.submit()
    root.update()
    assert str(window._button.cget("state")) == "disabled", "button not held"
    # Pressed again while the first is out: ignored, not a second sign-in.
    window.submit()
    # Still repainting while the stand-in is blocked on the thread.
    ticks = []
    root.after(10, lambda: ticks.append(1))
    wait_for(window, lambda: ticks)
    say("repaints while signing in", "yes")
    released.set()
    wait_for(window, lambda: str(window._button.cget("state")) == "normal")
    status = window._status.cget("text")
    assert status == REFUSED, f"status says {status!r}"
    assert not path.exists(), "a refused sign-in wrote the config"
    assert len(seen) == 1, f"signed in {len(seen)} times for one press"
    say("a refusal is shown", status)

    # Return submits, with what is in the boxes now.
    window.password.set("right")
    root.event_generate("<Return>", when="tail")
    wait_for(window, lambda: window.signed_in)
    assert seen[-1] == ("someone@example.org", "right"), f"sent {seen[-1]!r}"
    cfg = config_mod.load(path)
    assert cfg.account.email == "someone@example.org"
    assert cfg.account.password == "right"
    text = path.read_text(encoding="utf-8")
    assert "# LibreLinkUp password." in text, "the example's comments are gone"
    say("a sign-in writes the config", path.name)

    # And the window is gone, so the face can have the screen.
    try:
        root.winfo_exists()
    except Exception:
        pass
    else:
        raise AssertionError("the window stayed open after signing in")
    say("closes once signed in", "yes")


def show(directory: Path) -> None:
    path = directory / "config.toml"
    window = SignInWindow(path, example=paths.example_config(), try_account=stand_in([]))
    signed_in = window.run()
    print(f"signed in: {signed_in}")
    if signed_in:
        print(path.read_text(encoding="utf-8").split("[display]")[0])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        if "--show" in sys.argv[1:]:
            show(Path(tmp))
            return 0
        check(Path(tmp))
    print("sign-in window OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
