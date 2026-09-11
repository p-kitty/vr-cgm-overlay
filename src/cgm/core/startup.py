"""Starting with Windows: one shortcut in the user's Startup folder.

The resident process is meant to be started once and left, so the next
step is not having to start it at all. Windows opens everything in the
Startup folder at sign-in, and Task Manager lists it under Startup apps
with a switch -- so once this is installed, turning it off needs
nothing from this app.

A shortcut rather than a value under the registry's Run key, which
Windows treats the same way. This is software people install from a
repository, and a file they can see is one they can check and delete by
hand: `shell:startup` in the Run box opens the folder. A registry value
asks for the same trust with none of that, and "what did it change on
my machine" should have an answer anyone can find.

A .lnk is a binary format that only the Windows shell writes, so this
asks the shell for its ShellLink object and calls it directly, through
ctypes, one Unicode string per property.

Not through `WScript.Shell`, which is what this used to drive from
PowerShell. Its shortcut object passes every path through the ANSI
code page on the way in: on a Japanese Windows a Japanese path
survives, and anything that code page cannot spell -- Hangul, "café",
Japanese on an English Windows -- comes out as "?". Save then fails
when the Startup folder's path has such a character in it, and succeeds
silently when only the config's does, leaving a shortcut that names a
file that does not exist. ShellLink's wide interface has no code page
in the way at all.

What the shortcut runs is `pythonw.exe -m cgm`, from the environment
this was installed from. pythonw is the console-less interpreter every
venv on Windows carries, so a sign-in opens the face and nothing else.
With no console there is nowhere for an error to be printed, which is
why `cgm.main` shows the ones that stop the process in a dialog instead.

The config is named in full: the one registered is the one that was
checked when it was registered.
"""

from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager
from pathlib import Path

LINK_NAME = "vr-cgm-overlay.lnk"

DESCRIPTION = "FreeStyle Libre glucose on your desktop and in SteamVR"

CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

# Slots in each interface's vtable, counted from IUnknown's three.
# IShellLinkW is declared in shobjidl_core.h, IPersistFile in objidl.h.
_RELEASE = 2
_QUERY_INTERFACE = 0
_SET_DESCRIPTION = 7
_SET_WORKING_DIRECTORY = 9
_SET_ARGUMENTS = 11
_SET_PATH = 20
_SAVE = 6

try:
    from ctypes import wintypes

    _ole32 = ctypes.OleDLL("ole32")
except (AttributeError, OSError):  # not Windows
    _ole32 = None
else:

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # CoInitializeEx answers "already initialized differently" with an
    # error code that is not a failure here, so it is read by hand
    # rather than raised by OleDLL.
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    _ole32.CoUninitialize.restype = None
    _ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]
    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]


def startup_folder() -> Path:
    """The folder Windows opens everything in at sign-in, for this user."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("APPDATA is not set, so there is no Startup folder to use")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def pythonw(python: Path | None = None) -> Path:
    """The console-less interpreter beside the one running this."""
    return (python or Path(sys.executable)).with_name("pythonw.exe")


def arguments(config_path: Path) -> str:
    """What the shortcut passes to pythonw."""
    return f'-m cgm --config "{config_path.resolve()}"'


def _guid(text: str):
    guid = _GUID()
    _ole32.CLSIDFromString(text, ctypes.byref(guid))
    return guid


def _method(obj: ctypes.c_void_p, slot: int, restype, *argtypes):
    """One method of a COM object, looked up by its vtable slot.

    With `ctypes.HRESULT` as the result type a failure raises OSError on
    its own, so no call below has a return code to forget to check.
    """
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])


@contextmanager
def _com():
    """COM on this thread, for as long as the block runs."""
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        raise ctypes.WinError(hr)
    try:
        yield
    finally:
        # Only an initialize that succeeded is this block's to undo. A
        # thread that was already set up another way stays as it was.
        if hr >= 0:
            _ole32.CoUninitialize()


@contextmanager
def _released(obj: ctypes.c_void_p):
    try:
        yield obj
    finally:
        _method(obj, _RELEASE, wintypes.ULONG)(obj)


def save_link(link: Path, target: Path, arguments: str, working_directory: Path) -> None:
    """Write one shortcut, replacing any already there. OSError if it cannot."""
    with _com():
        shell_link = ctypes.c_void_p()
        _ole32.CoCreateInstance(
            ctypes.byref(_guid(CLSID_SHELL_LINK)),
            None,
            CLSCTX_INPROC_SERVER,
            ctypes.byref(_guid(IID_ISHELL_LINK_W)),
            ctypes.byref(shell_link),
        )
        with _released(shell_link):
            for slot, value in (
                (_SET_PATH, str(target)),
                (_SET_ARGUMENTS, arguments),
                (_SET_WORKING_DIRECTORY, str(working_directory)),
                (_SET_DESCRIPTION, DESCRIPTION),
            ):
                _method(shell_link, slot, ctypes.HRESULT, wintypes.LPCWSTR)(shell_link, value)
            persist = ctypes.c_void_p()
            _method(
                shell_link,
                _QUERY_INTERFACE,
                ctypes.HRESULT,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )(shell_link, ctypes.byref(_guid(IID_IPERSIST_FILE)), ctypes.byref(persist))
            with _released(persist):
                _method(persist, _SAVE, ctypes.HRESULT, wintypes.LPCWSTR, wintypes.BOOL)(
                    persist, str(link), True
                )


def install(config_path: Path, *, folder: Path | None = None) -> Path:
    """Put the shortcut in the Startup folder. Returns where it went.

    Installing again overwrites it, so it is also how to point it at a
    different config or a moved checkout.
    """
    if sys.platform != "win32":
        raise OSError("starting with Windows needs Windows")
    interpreter = pythonw()
    if not interpreter.exists():
        raise OSError(f"no pythonw.exe beside {sys.executable}")
    folder = folder or startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / LINK_NAME
    save_link(link, interpreter, arguments(config_path), config_path.resolve().parent)
    return link


def uninstall(*, folder: Path | None = None) -> bool:
    """Take the shortcut away. False if there was none."""
    link = (folder or startup_folder()) / LINK_NAME
    try:
        link.unlink()
    except FileNotFoundError:
        return False
    return True


def registered(*, folder: Path | None = None) -> Path | None:
    """The shortcut, if there is one."""
    link = (folder or startup_folder()) / LINK_NAME
    return link if link.exists() else None
