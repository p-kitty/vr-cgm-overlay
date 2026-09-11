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
session, whether a token expiry recovers on its own, whether
`trend.fast_mgdl_min` reads right against a real day. All of those are
`cgm.core` and `cgm.face` behaviour with no VR in them, and every one
of them used to be gated behind wearing a headset for as long as it
took to find out. They can be watched at a desk now. `NOTES.md` says
which.

tkinter is imported at module scope, which is why `cgm.main` imports
this module lazily: a Python without tkinter should still be able to run
`--dry-run` and the overlay.
"""

from __future__ import annotations

import ctypes
import logging
import re
import tkinter as tk

from PIL import Image, ImageTk

from cgm.face.renderer import CLEAR_COLUMN, WIDTH

log = logging.getLogger("vrcgm")

# The card is drawn translucent so the VR compositor can show the game
# through it. A window has nothing behind it to show, so the alpha is
# composited onto a flat backdrop rather than thrown away. The corners
# are square here -- an arc over nothing is just a bite out of the
# picture -- but the card itself is still translucent, `color_bg` being
# (14, 16, 22, 225), so what it is composited onto still tints it. A few
# shades off black rather than black, so that tint is a shade of the
# card and not a way of making it darker than it was drawn.
BACKDROP = (32, 34, 40)

# The face's own muted grey, the one "mg/dL" and "now" are drawn in. The
# corner marks borrow it so they read as part of the card rather than as
# something stuck on top of it.
CORNER_FG = "#7e838f"
CORNER_LIT = "#c8ccd4"

# The corner marks' type, sized at window.scale 1.0. In pixels, not
# points: they have to fit a column measured in the face's own pixels,
# and a point is a different number of those on every display. These
# are what the 13pt gear and 8pt badge came to at 96 dpi, so the marks
# look as they always did at 1.0 and scale with the face from there.
GEAR_FONT = ("Segoe UI Symbol", 17, "normal")
BADGE_FONT = ("Segoe UI", 11, "bold")

# How long the window has to sit still before where it is gets handed
# on to be written down. A drag is a Configure event per pixel, and one
# write at the end of it is the one that matters.
MOVE_SETTLE_MS = 1000

# A remembered position is only used if this point -- this far in from
# the window's top-left corner, on its title bar -- is on a monitor.
# The monitor it was left on may have been unplugged since, and a
# glucose readout that opens where nothing can show it has gone missing
# without saying so. The title bar, because that is what a window is
# dragged back by.
GRAB_INSET = (40, 12)

# What `wm geometry` reports: WxH+X+Y, with a coordinate left of or
# above the primary monitor written "+-50". A bare "-" would mean
# measured from the far edge instead, which Tk never reports back.
_GEOMETRY = re.compile(r"^\d+x\d+\+(-?\d+)\+(-?\d+)$")


def position_of(geometry: str) -> tuple[int, int] | None:
    """The top-left corner in a `wm geometry` string, or None."""
    match = _GEOMETRY.match(geometry)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def on_a_monitor(x: int, y: int) -> bool:
    """Whether a point on the desktop is on any monitor that is attached.

    Tk only knows the primary monitor's size, so this asks Windows.
    Neither this process nor Tk declares DPI awareness, so both see the
    same scaled coordinates and the question is asked in the units the
    position was written in. Anywhere but Windows there is nobody to
    ask, and the position is trusted.
    """
    try:
        user32 = ctypes.WinDLL("user32")
    except (AttributeError, OSError):
        return True
    from ctypes import wintypes

    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    MONITOR_DEFAULTTONULL = 0
    return bool(user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONULL))


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

    `position` is where it was last left, and `on_moved` is told each
    time it is left somewhere new. Neither knows where that is kept:
    `cgm.core.state` remembers it and `cgm.main` wires the two together.
    """

    def __init__(
        self,
        *,
        scale: float = 1.0,
        always_on_top: bool = True,
        position: tuple[int, int] | None = None,
    ) -> None:
        self._scale = scale
        self._closed = False
        self._interrupted = False
        # Tk drops a PhotoImage the moment nothing but the widget refers
        # to it, and then draws nothing. The reference has to be held
        # here, on the Python side, for as long as it is on screen.
        self._photo: ImageTk.PhotoImage | None = None
        # What size is currently on screen, so a change can be noticed.
        self._shown: tuple[int, int] | None = None
        # The corner marks: a gear to open the settings, and a badge
        # saying the overlay is up. Both are None until asked for, and
        # both sit on the card, so they carry its colour -- taken off
        # the picture rather than written down here, since the face
        # decides what colour it is.
        self._gear: tk.Label | None = None
        self._badge: tk.Label | None = None
        self._corner_bg = _hex(BACKDROP)
        self._vr = False

        self._root = tk.Tk()
        self._root.title("vr-cgm-overlay")
        self._root.configure(bg=_hex(BACKDROP))
        # The size is `window.scale`, so dragging the corner would only
        # letterbox the face inside a bigger frame.
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self.close)
        # Tk prints a failing callback's traceback straight to stderr and
        # carries on, past every logging handler. Through the log instead,
        # so it reaches the log file with everything else. It covers the
        # settings window too: every widget reports to its root.
        self._root.report_callback_exception = _log_callback_error
        self.set_always_on_top(always_on_top)

        self._label = tk.Label(
            self._root, bg=_hex(BACKDROP), bd=0, highlightthickness=0
        )
        self._label.pack()

        # Where the window is, as last seen; who to tell when it has been
        # left somewhere new; and the pending tell, while a drag is still
        # going on.
        self._position: tuple[int, int] | None = None
        self._moved = None
        self._pending_move: str | None = None
        if position is not None:
            x, y = position
            if on_a_monitor(x + GRAB_INSET[0], y + GRAB_INSET[1]):
                # Only the corner. The size is the image's, and set_image
                # forgetting the geometry keeps the position -- measured
                # rather than assumed: Tk moves nothing on `geometry("")`.
                self._root.geometry(f"+{x}+{y}")
                self._position = position
            else:
                log.info(
                    "the window was last left at %d,%d, which is on no "
                    "monitor now; opening where Windows puts it",
                    x,
                    y,
                )
        # add="+" so nothing else bound here is replaced. Bound on the
        # root, so every child's Configure arrives here too, and is
        # filtered out in _configured.
        self._root.bind("<Configure>", self._configured, add="+")

    # -- presentation -------------------------------------------------------

    def set_image(self, image: Image.Image) -> None:
        """Show a rendered face, resizing the window if it has to.

        Two things change the size, and neither one announces itself
        here: `window.scale`, and turning the history sparkline on or
        off, which gives the face a different height. So the size is
        measured off the image rather than tracked, and the window is
        told to forget its geometry whenever it differs -- without that
        Tk keeps the old size and crops or pads the new picture into it.
        """
        if self._closed:
            return
        shown = compose(image, self._scale)
        if shown.size != self._shown:
            self._shown = shown.size
            self._root.geometry("")
            self._place_marks()
        self._match_corner(shown)
        self._photo = ImageTk.PhotoImage(shown)
        self._label.configure(image=self._photo)

    def _match_corner(self, shown: Image.Image) -> None:
        """Sit the corner marks on whatever colour the card's corner is.

        Read off the top right pixel rather than named here. The card is
        translucent and this window flattens it onto BACKDROP, so the
        colour behind the marks is the result of both -- and the face is
        free to change its own without this file finding out. The top
        *left* would not do: that is where the in-range marker lights up.
        """
        corner = _hex(shown.getpixel((shown.width - 1, 0))[:3])
        if corner == self._corner_bg:
            return
        self._corner_bg = corner
        for mark in (self._gear, self._badge):
            if mark is not None:
                mark.configure(bg=corner)

    def set_scale(self, scale: float) -> None:
        """Resize, effective from the next image."""
        if self._closed:
            return
        self._scale = scale

    def set_always_on_top(self, on_top: bool) -> None:
        if self._closed:
            return
        self._root.attributes("-topmost", bool(on_top))

    def set_title(self, text: str) -> None:
        if self._closed:
            return
        self._root.title(text)

    def on_menu(self, callback) -> None:
        """Offer a way into a menu: a gear in the corner, or a right-click.

        Two ways in for one thing. The gear is the one somebody finds
        without being told; the right-click is the one that works
        wherever the pointer already is, once they have been.

        **A widget in this window's frame, not a mark on the face.** The
        face image is the same picture the overlay puts on a controller,
        where a gear cannot be clicked and would be a button that does
        nothing -- so it goes one step later, in the only place that is
        this frontend's alone. `place` rather than `pack`, because the
        window takes its size from the image and forgets its geometry
        whenever that changes, and a placed widget does not join in.

        `master` is this window, handed to the callback so a dialog can
        be parented to it -- a Toplevel needs one, and reaching in from
        outside for it would be worse than passing it out.
        """
        if self._closed:
            return

        def opened(_event=None) -> None:
            callback(self._root)

        self._label.bind("<Button-3>", opened)
        self._gear = self._mark("⚙")
        self._gear.configure(cursor="hand2")
        self._gear.bind("<Button-1>", opened)
        # Lit while the pointer is on it, which is the whole of saying
        # it is a control rather than a decoration.
        self._gear.bind("<Enter>", lambda _e: self._gear.configure(fg=CORNER_LIT))
        self._gear.bind("<Leave>", lambda _e: self._gear.configure(fg=CORNER_FG))
        self._place_marks()

    def set_vr(self, active: bool) -> None:
        """Show or hide the mark that says the overlay is on a controller.

        The window is the only place worth saying it. In the headset the
        answer is the face being on your wrist, and this frontend is
        otherwise identical whether SteamVR is running or not -- so
        without it the only way to know is the log.

        Not being up is not a fault: SteamVR may simply not be running,
        and the session waits for it. So it is a mark that appears rather
        than one that turns a colour, and its absence says nothing louder
        than "not yet".
        """
        if self._closed or active == self._vr:
            return
        self._vr = active
        if not active:
            if self._badge is not None:
                self._badge.place_forget()
            return
        if self._badge is None:
            self._badge = self._mark("VR")
        self._place_marks()

    def _place_marks(self) -> None:
        """Stand the gear and the VR mark in the face's clear column.

        `CLEAR_COLUMN` is the strip down the right edge that no status
        marker ever lights. Anywhere else these would sit on one: a label
        is opaque, so over a lit edge it cuts a dark box out of it, and a
        high used to put the VR mark in the middle of the top bar and the
        gear hard against its end, where both read as a notch in the bar
        rather than as marks of their own.

        Stacked, gear first -- it is the control, and the badge comes and
        goes -- with the badge in the gear's place when there is no gear.
        Everything is multiplied by the scale of the picture on screen,
        the type included, so they fit the column at every window.scale
        rather than only at 1.0. Called again whenever that size changes.
        """
        scale = self._shown[0] / WIDTH if self._shown else self._scale
        left, right = (edge * scale for edge in CLEAR_COLUMN)
        y = None
        for mark, (family, size, weight) in (
            (self._gear, GEAR_FONT),
            (self._badge if self._vr else None, BADGE_FONT),
        ):
            if mark is None:
                continue
            # Negative is pixels to Tk. Never zero, which is its default
            # size rather than none -- far too big for the column a
            # quarter-scale face leaves.
            mark.configure(font=(family, -max(1, round(size * scale)), weight))
            if y is None:
                # As much air above the first mark as there is either
                # side of it, measured down from where the stale frame
                # stops, so it clears the frame the way it clears the
                # bars -- by more than resampling blurs their edges.
                air = max(0, right - left - mark.winfo_reqwidth()) / 2
                y = scale * WIDTH - right + air
            mark.place(x=round((left + right) / 2), y=round(y), anchor="n")
            y += mark.winfo_reqheight()

    def _mark(self, text: str) -> tk.Label:
        """One of the small labels that stand in the face's clear column.

        No font yet: that depends on the scale, and `_place_marks` sets
        it every time it lays them out.
        """
        return tk.Label(
            self._root,
            text=text,
            fg=CORNER_FG,
            bg=self._corner_bg,
            bd=0,
            highlightthickness=0,
        )

    def on_moved(self, callback) -> None:
        """Have `callback((x, y))` called when the window is left somewhere.

        Once per move, a second after it stops, rather than once per
        pixel of the drag -- and once more on close if a move is still
        waiting, so quitting straight after a drag keeps it.
        """
        self._moved = callback

    def _configured(self, event) -> None:
        if event.widget is not self._root or self._closed:
            return
        # Minimised, Windows parks the window at -32000,-32000. That is
        # not a place it was left, and it is on no monitor.
        if self._root.state() != "normal":
            return
        position = position_of(self._root.geometry())
        if position is None or position == self._position:
            return
        first = self._position is None
        self._position = position
        # The first sighting of a window with nothing remembered is where
        # Windows chose to put it. Nothing has moved yet.
        if first or self._moved is None:
            return
        if self._pending_move is not None:
            self._root.after_cancel(self._pending_move)
        self._pending_move = self._root.after(MOVE_SETTLE_MS, self._settled)

    def _settled(self) -> None:
        self._pending_move = None
        if self._moved is not None and self._position is not None:
            self._moved(self._position)

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

        Anything else `tick` raises ends the loop just the same, because
        the next call is only scheduled once this one returns. So `tick`
        must not raise, and `cgm.main.Tick` is written not to.
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
        if self._pending_move is not None:
            # Quit before the move settled. It is still where it was left.
            try:
                self._root.after_cancel(self._pending_move)
            except tk.TclError:
                pass
            self._settled()
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


def _log_callback_error(exc_type, exc, tb) -> None:
    """What Tk calls when a callback raises, in place of printing it."""
    log.error("a window callback failed", exc_info=(exc_type, exc, tb))
