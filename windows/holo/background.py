# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Background running on Windows: tray icon, start with Windows, single instance.

Holo cannot be a real Windows service: services run in an isolated session with no
access to the user's microphone permission, desktop, clipboard or keyboard, so the
actions could not work. Instead it behaves like most antivirus tray agents: closing
the window keeps it running next to the clock, and it can start at sign-in.
"""

from __future__ import annotations

import atexit
import sys
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"
APP_ID = "Holo.DeskTap"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Holo"
ASSETS = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS / "holo.ico"
ICON_PNG = ASSETS / "holo-256.png"


# ------------------------------------------------------------- taskbar identity

def set_app_user_model_id() -> None:
    """Makes Windows show Holo's own icon in the taskbar instead of Python's."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- start at login

def launch_command() -> str:
    """Command line that starts Holo hidden in the tray."""
    if getattr(sys, "frozen", False):  # packaged Holo.exe
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return f'"{interpreter}" "{launcher}" --background'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not IS_WINDOWS:
        raise OSError("Start with Windows is only available on Windows.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------- single instance

class SingleInstance:
    """A named mutex marks the running copy; a named event asks it to show its window."""

    MUTEX = "Local\\HoloDeskTap.Instance"
    EVENT = "Local\\HoloDeskTap.Show"

    def __init__(self):
        self._mutex = None
        self._event = None
        self.is_primary = True
        if not IS_WINDOWS:
            return
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateEventW.restype = ctypes.c_void_p
        self._k32 = k32
        self._mutex = k32.CreateMutexW(None, False, self.MUTEX)
        self.is_primary = k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
        self._event = k32.CreateEventW(None, False, False, self.EVENT)  # auto-reset

    def signal_primary(self) -> None:
        if self._event:
            self._k32.SetEvent(ctypes_handle(self._event))

    def show_requested(self) -> bool:
        if not self._event:
            return False
        return self._k32.WaitForSingleObject(ctypes_handle(self._event), 0) == 0


def ctypes_handle(value):
    import ctypes
    return ctypes.c_void_p(value)


# ---------------------------------------------------------------------- tray icon

# Windows identifies a notification icon by its program and an ID number. Holo's program
# is the shared pythonw.exe, and pystray 0.19 means to pass a per-object ID but names the
# field "hID" instead of "uID", so the ID is always 0, the same as any other Python tray
# app. A distinctive fixed ID gives Holo its own stable entry.
TRAY_ICON_ID = 0x484F4C4F  # "HOLO"


def make_hicon(image, size: Optional[int] = None):
    """Builds a Windows icon handle straight from a PIL image (32-bit color with alpha).

    pystray instead writes the image to a temporary .ico (Pillow stores PNG-compressed
    frames) and asks Windows to load it from disk; building the icon in memory at the
    exact notification-area size avoids that file round trip. Returns None on failure.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32  # type: ignore[attr-defined]
        size = size or user32.GetSystemMetrics(49) or 16          # SM_CXSMICON (DPI aware)
        rgba = image.convert("RGBA").resize((size, size), Image.LANCZOS)
        r, g, b, a = rgba.split()
        bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                        ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.CreateBitmap.restype = wintypes.HBITMAP
        gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        user32.CreateIconIndirect.restype = wintypes.HICON
        user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]

        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size,
                                  biPlanes=1, biBitCount=32, biCompression=0)  # top-down BI_RGB
        bits = ctypes.c_void_p()
        color = gdi32.CreateDIBSection(None, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not color or not bits.value:
            return None
        ctypes.memmove(bits, bgra, len(bgra))
        mask = gdi32.CreateBitmap(size, size, 1, 1, None)
        try:
            info = ICONINFO(fIcon=True, xHotspot=0, yHotspot=0, hbmMask=mask, hbmColor=color)
            return user32.CreateIconIndirect(ctypes.byref(info)) or None
        finally:
            gdi32.DeleteObject(color)
            if mask:
                gdi32.DeleteObject(mask)
    except Exception:
        return None


def _icon_class(pystray):
    """pystray's Icon with a fixed notification-icon ID on Windows."""
    if not IS_WINDOWS:
        return pystray.Icon
    try:
        import ctypes
        from pystray._util import win32
    except Exception:
        return pystray.Icon

    class HoloIcon(pystray.Icon):
        def _assert_icon_handle(self):
            if not self._icon_handle:
                self._icon_handle = make_hicon(self.icon)
            if not self._icon_handle:
                super()._assert_icon_handle()  # pystray's own .ico route as a fallback

        def reassert_icon(self):
            """Sends Windows a freshly built icon again (repairs a swapped tray image)."""
            if self.visible:
                self._update_icon()

        def _message(self, code, flags, **kwargs):
            win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
                cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
                hWnd=self._hwnd,
                uID=TRAY_ICON_ID,
                uFlags=flags,
                **kwargs))

    return HoloIcon


class TrayIcon:
    """Notification-area icon. Callbacks run on the tray thread; the app marshals them."""

    def __init__(self, on_open: Callable[[], None], on_toggle_listen: Callable[[], None],
                 is_listening: Callable[[], bool], on_toggle_actions: Callable[[], None],
                 actions_enabled: Callable[[], bool], on_toggle_autostart: Callable[[], None],
                 on_exit: Callable[[], None]):
        self.available = False
        self._icon = None
        try:
            import pystray
            from PIL import Image
        except Exception:
            return
        item, menu = pystray.MenuItem, pystray.Menu
        try:
            self._icon = _icon_class(pystray)(
                "Holo",
                Image.open(ICON_PNG),
                "Holo - desk taps",
                menu=menu(
                    item("Open Holo", lambda *_: on_open(), default=True),
                    menu.SEPARATOR,
                    item("Listening", lambda *_: on_toggle_listen(), checked=lambda _: is_listening()),
                    item("Run actions", lambda *_: on_toggle_actions(), checked=lambda _: actions_enabled()),
                    item("Start with Windows", lambda *_: on_toggle_autostart(),
                         checked=lambda _: autostart_enabled(), visible=IS_WINDOWS),
                    menu.SEPARATOR,
                    item("Exit Holo", lambda *_: on_exit()),
                ),
            )
        except Exception:
            return
        try:
            self._icon.run_detached()
            self.available = True
        except Exception:
            self._icon = None
            return
        # Last line of defence: remove the icon whenever Python exits.
        atexit.register(self.stop)

    def refresh(self) -> None:
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def reassert(self) -> None:
        """Re-sends Holo's icon to Windows so a wrongly displayed image corrects itself."""
        if self._icon is not None and hasattr(self._icon, "reassert_icon"):
            try:
                self._icon.reassert_icon()
            except Exception:
                pass

    def notify(self, message: str, title: str = "Holo") -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def stop(self) -> None:
        """Removes the icon right away, then ends the tray thread.

        pystray only removes the icon from its own thread after the stop message is
        processed, which can be too late if the process ends first (sign-out, shutdown);
        the icon then lingers or confuses Windows' icon memory. Removing it here first
        makes that impossible. Safe to call more than once.
        """
        icon, self._icon = self._icon, None
        if icon is None:
            return
        try:
            icon._hide()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass


def apply_window_icon(root) -> None:
    """Window and taskbar icon for a Tk root."""
    try:
        if IS_WINDOWS and ICON_ICO.exists():
            root.iconbitmap(default=str(ICON_ICO))
        elif ICON_PNG.exists():
            import tkinter as tk
            root._holo_icon = tk.PhotoImage(file=str(ICON_PNG))
            root.iconphoto(True, root._holo_icon)
    except Exception:
        pass
