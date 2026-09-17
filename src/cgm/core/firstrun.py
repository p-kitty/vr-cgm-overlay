"""The first run: no account yet, so ask for one before anything starts.

Downloaded and double-clicked, the app used to open a dialog saying
config.toml was not found and to stop there -- which is correct, and
useless to anyone who has never opened a TOML file. So a run that has no
account to sign in with asks for one instead, in a window
(`cgm.desk.signin`), and this is the half of that which needs no window.

**Nothing is written until the account has actually worked.** The
address and password are tried against LibreLinkUp first, all the way to
a reading, so what goes into config.toml is known to show a number. A
wrong password, or an account that follows nobody, is said on the window
while the person who typed it is still looking at it -- rather than
coming up as AUTH ERROR on the face and backing off for ten minutes.

**The file is started from config.example.toml**, comments and all, so
someone who later opens it finds every setting explained beside it,
exactly as someone who copied it by hand would.

What counts as "no account" is deliberately narrow: the file is missing,
or its email or password is empty or still the example's placeholder.
Anything else wrong with a config is a mistake in a file somebody has
been editing, and is reported as one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomlkit

from cgm.core import config as config_mod
from cgm.core.librelink import AuthError, LibreLinkError, LibreLinkUp

# The address config.example.toml ships with. Left in place it is not an
# account, however much it looks like one.
PLACEHOLDER_EMAIL = "you@example.com"


def needs_account(path: Path) -> bool:
    """True when there is nothing to sign in with yet."""
    if not path.exists():
        return True
    try:
        with path.open("rb") as fh:
            account = tomllib.load(fh).get("account") or {}
    except (OSError, tomllib.TOMLDecodeError):
        # A broken file is not a first run; loading it says what is wrong.
        return False
    if not isinstance(account, dict):
        return False
    email = account.get("email")
    password = account.get("password")
    return not email or email == PLACEHOLDER_EMAIL or not password


def saved_email(path: Path) -> str:
    """The address already in the file, to fill the box in with."""
    try:
        with path.open("rb") as fh:
            email = (tomllib.load(fh).get("account") or {}).get("email")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return ""
    if not isinstance(email, str) or email == PLACEHOLDER_EMAIL:
        return ""
    return email


def try_account(email: str, password: str, *, client_class=LibreLinkUp) -> str | None:
    """Sign in and fetch one reading. None when it worked, else why not.

    One fetch, the same one the poller makes once a minute, and only when
    somebody presses the button -- so this is no harder on the API than
    starting the app is.

    The reason is a sentence for the window, not a traceback: this is
    the one place a person who has never seen a log is told what went
    wrong.
    """
    email = email.strip()
    if not email or not password:
        return "Enter both the email and the password."
    try:
        client_class(email, password).get_latest()
    except AuthError as exc:
        return (
            "LibreLinkUp did not accept that sign-in. Check it is the email and "
            f"password of the LibreLinkUp app, not LibreLink. ({exc})"
        )
    except LibreLinkError as exc:
        # Signed in, and then no reading: most often nobody is followed.
        return f"Signed in, but no reading came back: {exc}"
    except OSError as exc:
        # requests' errors are OSErrors: no network, DNS, a timeout.
        return f"Could not reach LibreLinkUp. Check the internet connection. ({exc})"
    return None


def write_account(path: Path, email: str, password: str, *, example: Path) -> None:
    """Put the account into config.toml, creating it from the example.

    An existing file keeps everything else in it; only the two values
    change. Written through a temporary file and then loaded back, so a
    file this produces is one the app starts from -- and if it would not
    be, the ValueError says why and the old file is still in place.
    """
    if path.exists():
        text = path.read_text(encoding="utf-8")
    elif example.exists():
        text = example.read_text(encoding="utf-8")
    else:
        # A build missing its example still has to start. Defaults for
        # everything, which is what an empty section means anyway.
        text = ""
    document = tomlkit.parse(text)
    if "account" not in document:
        document["account"] = tomlkit.table()
    document["account"]["email"] = email.strip()
    document["account"]["password"] = password

    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".new")
    scratch.write_text(tomlkit.dumps(document), encoding="utf-8")
    try:
        # Checked before it replaces anything, for the same reason
        # `config.save` validates first.
        config_mod.load(scratch)
    except ValueError:
        scratch.unlink(missing_ok=True)
        raise
    scratch.replace(path)
