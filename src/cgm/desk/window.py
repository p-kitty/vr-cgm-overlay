"""The watch face in a desktop window.

The same image the overlay hands to SteamVR, drawn in a small
always-on-top window instead. It is the presentation half only: it takes
an image and shows it, exactly as `cgm.vr.overlay.WristOverlay` takes an
image and puts it on a controller. Neither one fetches anything or reads
the config; `cgm.main` wires both.

Two reasons it exists.

The obvious one is that not everything worth watching happens in VR. On
a second monitor this is the whole application minus the headset.

The other is verification. Several things about this app can only be
judged over hours -- whether the fetch schedule holds across a long
session, whether a token expiry recovers on its own, whether an hour is
the right trend window against a real day. All of those are `cgm.core`
and `cgm.face` behaviour with no VR in them, and every one of them used
to be gated behind wearing a headset for as long as it took to find out.
They can be watched at a desk now. `NOTES.md` says which.

tkinter is imported at module scope, which is why `cgm.main` imports
this module lazily: a Python without tkinter should still be able to run
`--dry-run` and the overlay.
"""

from __future__ import annotations

import logging
import tkinter as tk

from PIL import Image, ImageTk

log = logging.getLogger("vrcgm")

# The card is drawn translucent so the VR compositor can show the game
# through it. A window has nothing behind it to show, so the alpha is
# composited onto a flat backdrop rather than thrown away. A few shades
# off black, not black: the card is nearly black itself, and its rounded
# corners only read as a shape against something it is not.
BACKDROP = (32, 34, 40)


def compose(face: Image.Image, scale: float) -> Image.Image:
    """Flatten the face onto the window backdrop at the asked-for size.

    Scaling happens here, once per frame, rather than by rendering the
    face at a different size: the layout is tuned at 512x256 -- font
    sizes, the marker thickness, where the arrow sits next to the digits
    -- and re-deriving all of that per scale would be a second layout to
    keep in step with the first.
    """
    canvas = Image.new("RGBA", face.size, (*BACKDROP, 255))
    canvas.alpha_composite(face.convert("RGBA"))
    flat = canvas.convert("RGB")
    if scale == 1.0:
        return flat
    size = (max(1, round(flat.width * scale)), max(1, round(flat.height * scale)))
    # LANCZOS both ways. The face is mostly large flat digits, and the
    # cheaper filters fringe their edges at the fractional scales
    # somebody actually picks.
    return flat.resize(size, Image.Resampling.LANCZOS)


class FaceWindow:
    """A borderless-feeling window holding one watch face.

    Usage mirrors the overlay's:

        with FaceWindow(scale=1.0, always_on_top=True) as win:
            win.set_image(image)
            win.run(tick, interval_ms=1000)
    """

    def __init__(self, *, scale: float = 1.0, always_on_top: bool = True) -> None:
        self._scale = scale
        self._closed = False
        self._interrupted = False
        # Tk drops a PhotoImage the moment nothing but the widget refers
        # to it, and then draws nothing. The reference has to be held
        # here, on the Python side, for as long as it is on screen.
        self._photo: ImageTk.PhotoImage | None = None

        self._root = tk.Tk()
        self._root.title("vr-cgm-overlay")
        self._root.configure(bg=_hex(BACKDROP))
        # The size is `window.scale`, so dragging the corner would only
        # letterbox the face inside a bigger frame.
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self.close)
        self.set_always_on_top(always_on_top)

        self._label = tk.Label(
            self._root, bg=_hex(BACKDROP), bd=0, highlightthickness=0
        )
        self._label.pack()

    # -- presentation -------------------------------------------------------

    def set_image(self, image: Image.Image) -> None:
        """Show a rendered face."""
        if self._closed:
            return
        self._photo = ImageTk.PhotoImage(compose(image, self._scale))
        self._label.configure(image=self._photo)

    def set_scale(self, scale: float) -> None:
        """Resize, effective from the next image.

        The window is told to forget its size so it takes the new
        image's; without that it keeps the old one and crops or pads.
        """
        if scale == self._scale or self._closed:
            return
        self._scale = scale
        self._root.geometry("")

    def set_always_on_top(self, on_top: bool) -> None:
        if self._closed:
            return
        self._root.attributes("-topmost", bool(on_top))

    def set_title(self, text: str) -> None:
        if self._closed:
            return
        self._root.title(text)

    # No `pulse` here. The overlay has one because it has a controller
    # to buzz, and a window does not; the channel a window can use is
    # sound, which `cgm.core.alert` owns and plays for both frontends.
    # Nothing about announcing a low belongs in this class.

    # -- lifecycle ----------------------------------------------------------

    def should_quit(self) -> bool:
        return self._closed

    def run(self, tick, *, interval_ms: int) -> None:
        """Call `tick` every `interval_ms` until the window closes.

        Tk owns the loop, so the caller's work is handed to it rather
        than the other way round.

        Ctrl-C needs the help. Tk swallows exceptions raised inside a
        callback -- it reports them and carries on -- so a
        KeyboardInterrupt landing in `tick` would print a traceback and
        leave the window up. It is caught here, closes the window, and
        is raised again once the loop is properly unwound.
        """

        def wrapped() -> None:
            if self._closed:
                return
            try:
                tick()
            except KeyboardInterrupt:
                self._interrupted = True
                self.close()
                return
            if not self._closed:
                self._root.after(interval_ms, wrapped)

        self._root.after(0, wrapped)
        try:
            self._root.mainloop()
        except KeyboardInterrupt:
            # Raised between callbacks rather than inside one.
            self._interrupted = True
            self.close()
        if self._interrupted:
            raise KeyboardInterrupt

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._photo = None
        try:
            self._root.destroy()
        except tk.TclError:
            # Already gone, which is not a problem worth reporting.
            pass

    def __enter__(self) -> "FaceWindow":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _hex(rgb: tuple[int, int, int]) -> str:
    """Tk wants colours as #rrggbb, and Pillow hands them over as tuples."""
    return "#%02x%02x%02x" % rgb
