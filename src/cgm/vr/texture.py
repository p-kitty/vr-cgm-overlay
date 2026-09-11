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

Imports nothing from SteamVR: it is handed the overlay interface, so it
can be driven by a stand-in with no headset in the room.
"""

from __future__ import annotations

import logging
import tempfile
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
