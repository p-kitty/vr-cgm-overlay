"""Handing overlay textures to the compositor as files.

`setOverlayRaw` is the obvious call -- the face is already pixels in
memory -- and it is what this used until it turned out to have a
ceiling. Each call leaves a shared memory block that SteamVR counts
against the process and, as far as the logs show, never gives back. At
200 it refuses the next one, `setOverlayRaw` raises
`OverlayError_RequestFailed`, and that ended the VR half. The face
changes about twice a minute -- a new reading, the age readout ticking
over -- so it happened about 105 minutes into every session. vrclient's
own log says so in as many words: "Refusing to create memory block
because 201 blocks are already outstanding".

`setOverlayFromFile` has the compositor read the image itself, so there
is no block on this side to count. Writing a 512x440 PNG at the fastest
compression takes a few milliseconds, and happens when the face changes,
not every frame.

Two files, taken in turn, rather than one:

- the compositor loads asynchronously, so rewriting the file it was just
  handed could give it half of the next frame. The other one was handed
  over a draw ago, which is seconds at the very least.
- if SteamVR skipped a load because the path matched the last one, the
  face would stop changing. Alternating means two calls in a row never
  name the same path.

Not a fresh name per draw either. Were the compositor to keep a texture
per path, that would grow without bound and quietly, which is exactly
the failure this module exists to get away from. Two names that turned
out wrong would be wrong at once, where it can be seen.

A file costs one thing raw bytes did not: the overlay goes blank while
the compositor loads it, and in the headset that was a blink at every
change. So the face is two overlays in one place (`DoubleBuffer`): the
next frame loads into the hidden one, and they swap once it has.

Imports nothing from SteamVR: it is handed the overlay interface, so it
can be driven by a stand-in with no headset in the room.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

# One directory for every run, not one per run: the one-copy lock means
# only one process writes here, and a fixed place leaves two small files
# behind a crash rather than a new directory each time.
TEXTURE_DIR_NAME = "vr-cgm-overlay"

# zlib's fastest. The file lives for one load and is read from the same
# disk it was written to, so size buys nothing and time is the VR thread's.
PNG_COMPRESS_LEVEL = 1

# How long to wait for the compositor to say a texture has loaded before
# showing it regardless. A 12 KB PNG off a local disk takes milliseconds;
# this is only for an answer that never comes, which would otherwise
# leave the face stuck on the frame before.
LOAD_TIMEOUT_SEC = 1.0


def texture_dir(root: Path | None = None) -> Path:
    """Where texture files go, created if it is not there yet.

    Under the temp directory by default. SteamVR has been reported not
    to open paths with characters outside ASCII, and this checkout lives
    under デスクトップ, so the project directory is no place for them.
    The temp directory usually is plain ASCII; when it is not, say so,
    because the face will then never appear and nothing else will say why.
    """
    path = (root or Path(tempfile.gettempdir())) / TEXTURE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    if not str(path).isascii():
        log.warning(
            "texture files go to %s, which SteamVR may not be able to open", path
        )
    return path


def hand_over(overlay, handle, image: Image.Image, path: Path) -> None:
    """Write `image` to `path` and have the compositor load it from there."""
    image.save(path, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    # Absolute: the compositor is another process, with its own idea of
    # the working directory.
    overlay.setOverlayFromFile(handle, str(path.resolve()))


class TextureFiles:
    """One overlay's texture, handed over through two files in turn.

    `set` does nothing when the pixels match what was last handed over.
    The draw loop hands over a frame every second so the age readout
    stays current, but the face itself changes about twice a minute.
    """

    def __init__(self, overlay, handle, directory: Path, stem: str) -> None:
        self._overlay = overlay
        self._handle = handle
        self._paths = (directory / f"{stem}-a.png", directory / f"{stem}-b.png")
        self._turn = 0
        # Size and pixels of what is on screen, or None before the first.
        self._shown: tuple[tuple[int, int], bytes] | None = None
        # The file the compositor was last pointed at, for a load that
        # fails to be able to name it.
        self.last_path: Path | None = None

    def set(self, image: Image.Image) -> bool:
        """Show `image`. True if it was handed over, False if unchanged."""
        shown = (image.size, image.tobytes())
        if shown == self._shown:
            return False

        path = self._paths[self._turn]
        hand_over(self._overlay, self._handle, image, path)
        self._turn = 1 - self._turn
        self._shown = shown
        self.last_path = path
        return True

    def forget(self) -> None:
        """Take the last frame as not shown, so the next `set` hands it over.

        For a load the compositor reported failing: what it holds is not
        what was handed over, and matching against that would skip the
        retry.
        """
        self._shown = None


class DoubleBuffer:
    """The face as two overlays in one place, so a change never blinks.

    Handing the compositor a file blanks the overlay until the load is
    done. So the next frame goes into the hidden overlay, and the two
    swap only once the compositor says it has loaded -- the new one
    shown before the old one is hidden, so no frame has neither.

    The caller owns the event queues: it reads both overlays' and passes
    on what they say through `loaded` and `failed`, and calls `poll` on
    every pass so a load nobody answers still ends. That keeps the
    SteamVR event types on the far side of this module.

    Each overlay alternates between two files of its own, so neither is
    handed the path it holds, and a file is only rewritten while its
    overlay is hidden.
    """

    def __init__(
        self,
        overlay,
        handles: tuple,
        directory: Path,
        *,
        timeout_sec: float = LOAD_TIMEOUT_SEC,
        clock=time.monotonic,
    ) -> None:
        self._overlay = overlay
        self._front, self._back = handles
        self._files = {
            handle: TextureFiles(overlay, handle, directory, f"face-{name}")
            for handle, name in zip(handles, "ab")
        }
        self._timeout = timeout_sec
        self._clock = clock
        # Size and pixels of the front overlay's frame, and of the one
        # loading into the back, if any.
        self._shown: tuple[tuple[int, int], bytes] | None = None
        self._loading: tuple[tuple[int, int], bytes] | None = None
        self._loading_since: float | None = None
        # The newest frame that arrived while another was loading. Only
        # the newest: one that was replaced before it could be shown has
        # nothing left to say.
        self._pending: Image.Image | None = None
        self._said_timeout = False
        overlay.showOverlay(self._front)

    @property
    def handles(self) -> tuple:
        """Both overlays, shown one first."""
        return (self._front, self._back)

    def set(self, image: Image.Image) -> None:
        """Show `image` as soon as it has loaded. Unchanged pixels do nothing."""
        if self._loading_since is not None:
            self._pending = image
            return
        frame = (image.size, image.tobytes())
        if frame == self._shown:
            return
        self._loading = frame
        if self._files[self._back].set(image):
            self._loading_since = self._clock()
        else:
            # The hidden overlay already holds exactly this, from two
            # frames ago. Nothing to wait for.
            self._swap()

    def loaded(self, handle) -> None:
        """The compositor has finished loading a texture into `handle`."""
        if handle == self._back and self._loading_since is not None:
            self._swap()

    def failed(self, handle) -> None:
        """The compositor could not load the texture handed to `handle`."""
        if handle != self._back or self._loading_since is None:
            return
        files = self._files[handle]
        log.warning("SteamVR could not load the face from %s", files.last_path)
        files.forget()
        self._loading = None
        self._loading_since = None
        # Keep the face that is up, and move on to whatever came next.
        self._next()

    def poll(self) -> None:
        """Show a load that has gone unanswered too long regardless."""
        if self._loading_since is None:
            return
        if self._clock() - self._loading_since < self._timeout:
            return
        if not self._said_timeout:
            # Once: if the compositor never answers, it never will, and
            # the face blinking again is the whole of the damage.
            log.warning(
                "SteamVR did not say the face had loaded within %.1fs; "
                "showing it regardless",
                self._timeout,
            )
            self._said_timeout = True
        self._swap()

    def _swap(self) -> None:
        # Show, then hide. The other way round leaves a frame with nothing.
        self._overlay.showOverlay(self._back)
        self._overlay.hideOverlay(self._front)
        self._front, self._back = self._back, self._front
        self._shown = self._loading
        self._loading = None
        self._loading_since = None
        self._next()

    def _next(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            self.set(pending)
