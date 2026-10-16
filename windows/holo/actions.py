# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Windows implementations of the per-zone actions.

Replaces the macOS LocalActionDispatcher (NSSound, NSPasteboard, NSWorkspace,
/bin/zsh, screencapture, Shortcuts) with Windows equivalents.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, asdict

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

ACTION_KINDS = {
    "none": "Visual only",
    "sound": "Play sound",
    "copyText": "Copy text",
    "speakText": "Speak text",
    "openURL": "Open website",
    "openItem": "Open app, file or folder",
    "runCommand": "Run command (cmd.exe)",
    "runPowerShell": "Run PowerShell",
    "pressKeys": "Press keyboard shortcut",
    "screenshotClipboard": "Screenshot to clipboard",
    "screenshotSelection": "Snip a region (Snipping Tool)",
}

SYSTEM_SOUNDS = ["SystemAsterisk", "SystemExclamation", "SystemHand", "SystemQuestion",
                 "SystemDefault", "SystemNotification"]

# What the single text field means for each kind, shown in the Actions editor.
TEXT_HINTS = {
    "sound": "System sound name (e.g. SystemAsterisk) or path to a .wav file",
    "copyText": "Text to put on the clipboard",
    "speakText": "Text to speak aloud",
    "openURL": "Website address, e.g. https://claude.ai",
    "openItem": "Path to a program (.exe / .lnk), file or folder",
    "runCommand": "Command, e.g.  start notepad",
    "runPowerShell": "PowerShell script, e.g.  Start-Process calc",
    "pressKeys": "Keys joined by +, e.g. ctrl+shift+esc, win+d, playpause",
}


@dataclass
class ZoneAction:
    kind: str = "none"
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ZoneAction":
        kind = d.get("kind", "none")
        return ZoneAction(kind if kind in ACTION_KINDS else "none", d.get("text", ""))

    @property
    def is_configured(self) -> bool:
        if self.kind in ("none", "screenshotClipboard", "screenshotSelection"):
            return True
        return bool(self.text.strip())


class ActionError(Exception):
    pass


def _powershell(script: str, sta: bool = False) -> None:
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if sta:
        args.append("-STA")
    args += ["-Command", script]
    subprocess.Popen(args, creationflags=CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def screenshot_to_clipboard() -> tuple[int, int]:
    """Copies all monitors to the clipboard as a bitmap and returns its size.

    Done in-process with GDI instead of a PowerShell helper: PowerShell took seconds to
    start (so a quick paste still got the old clipboard) and, not being DPI aware, saw a
    scaled-down screen and captured only part of it on displays set above 100%.
    """
    import ctypes
    from ctypes import wintypes

    user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = ctypes.c_void_p
    for fn, res, args in (
        (user32.GetDC, handle, [handle]),
        (user32.ReleaseDC, ctypes.c_int, [handle, handle]),
        (gdi32.CreateCompatibleDC, handle, [handle]),
        (gdi32.CreateCompatibleBitmap, handle, [handle, ctypes.c_int, ctypes.c_int]),
        (gdi32.SelectObject, handle, [handle, handle]),
        (gdi32.BitBlt, wintypes.BOOL, [handle, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       handle, ctypes.c_int, ctypes.c_int, wintypes.DWORD]),
        (gdi32.GetDIBits, ctypes.c_int, [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.UINT]),
        (gdi32.DeleteObject, wintypes.BOOL, [handle]),
        (gdi32.DeleteDC, wintypes.BOOL, [handle]),
        (kernel32.GlobalAlloc, handle, [wintypes.UINT, ctypes.c_size_t]),
        (kernel32.GlobalLock, ctypes.c_void_p, [handle]),
        (kernel32.GlobalUnlock, wintypes.BOOL, [handle]),
        (kernel32.GlobalFree, handle, [handle]),
        (user32.OpenClipboard, wintypes.BOOL, [handle]),
        (user32.EmptyClipboard, wintypes.BOOL, []),
        (user32.SetClipboardData, handle, [wintypes.UINT, handle]),
        (user32.CloseClipboard, wintypes.BOOL, []),
    ):
        fn.restype, fn.argtypes = res, args

    # Physical pixels on every monitor, whatever the display scaling.
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except AttributeError:
        pass
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)   # SM_X/YVIRTUALSCREEN
    w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)   # SM_CX/CYVIRTUALSCREEN
    if w <= 0 or h <= 0:
        raise ActionError("Could not read the screen size.")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    stride = (w * 3 + 3) & ~3                      # 24-bit rows padded to 4 bytes
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=h, biPlanes=1,
                              biBitCount=24, biCompression=0, biSizeImage=stride * h)
    pixels = ctypes.create_string_buffer(stride * h)
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    try:
        previous = gdi32.SelectObject(memory, bitmap)
        copied = gdi32.BitBlt(memory, 0, 0, w, h, screen, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY|CAPTUREBLT
        gdi32.SelectObject(memory, previous)
        rows = gdi32.GetDIBits(memory, bitmap, 0, h, pixels, ctypes.byref(header), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    if not copied or rows != h:
        raise ActionError("Windows did not allow capturing the screen.")

    data = bytes(header) + pixels.raw
    block = kernel32.GlobalAlloc(0x0002, len(data))                 # GMEM_MOVEABLE
    if not block:
        raise ActionError("Not enough memory for the screenshot.")
    ctypes.memmove(kernel32.GlobalLock(block), data, len(data))
    kernel32.GlobalUnlock(block)
    for _ in range(20):                                             # another app may hold the clipboard
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        kernel32.GlobalFree(block)
        raise ActionError("The clipboard is busy; try again.")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(8, block):                   # CF_DIB; the clipboard owns it now
            kernel32.GlobalFree(block)
            raise ActionError("Could not put the screenshot on the clipboard.")
    finally:
        user32.CloseClipboard()
    return w, h


def perform(action: ZoneAction, clipboard_setter=None) -> None:
    """Run an action. Raises ActionError with a user-readable message."""
    kind, text = action.kind, action.text.strip()
    if kind == "none" or not action.is_configured:
        return
    if not IS_WINDOWS and kind not in ("openURL", "copyText"):
        raise ActionError("This action is only available on Windows.")

    try:
        if kind == "sound":
            import winsound
            if text.lower().endswith(".wav"):
                winsound.PlaySound(text, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound(text or "SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif kind == "copyText":
            if clipboard_setter is None:
                raise ActionError("Clipboard is unavailable.")
            clipboard_setter(action.text)
        elif kind == "speakText":
            _powershell("Add-Type -AssemblyName System.Speech; "
                        "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("
                        + _ps_quote(action.text) + ")")
        elif kind == "openURL":
            url = text if "://" in text else "https://" + text
            if not url.lower().startswith(("http://", "https://")):
                raise ActionError("Only http and https addresses are supported.")
            webbrowser.open(url)
        elif kind == "openItem":
            path = os.path.expandvars(os.path.expanduser(text.strip('"')))
            if not os.path.exists(path):
                raise ActionError(f"Not found: {path}")
            os.startfile(path)  # type: ignore[attr-defined]
        elif kind == "runCommand":
            subprocess.Popen(action.text, shell=True, creationflags=CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "runPowerShell":
            _powershell(action.text)
        elif kind == "pressKeys":
            press_keys(text)
        elif kind == "screenshotClipboard":
            screenshot_to_clipboard()
        elif kind == "screenshotSelection":
            os.startfile("ms-screenclip:")  # type: ignore[attr-defined]
    except ActionError:
        raise
    except Exception as exc:  # surface any OS failure in the UI
        raise ActionError(f"{ACTION_KINDS.get(kind, kind)} failed: {exc}") from exc


# ------------------------------------------------------------ keyboard input

_VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "playpause": 0xB3,
}
_VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_EXTENDED = {0x5B, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2C,
             0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


_MODIFIER_ORDER = (0x11, 0x12, 0x10, 0x5B)   # Ctrl, Alt, Shift, Win


def parse_keys(spec: str) -> list[int]:
    codes = []
    for part in [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]:
        if part in _VK:
            codes.append(_VK[part])
        elif len(part) == 1 and (part.isalnum()):
            codes.append(ord(part.upper()))
        else:
            raise ActionError(f"Unknown key: {part}")
    if not codes:
        raise ActionError("No keys given.")
    # Modifiers first, always in the same order, so "shift+alt" acts exactly like
    # "alt+shift". Pressing Shift before Alt meant Alt went down and up with no key pressed
    # under it, which Windows treats as a lone Alt press (menus / key tips open).
    modifiers = [vk for vk in _MODIFIER_ORDER if vk in codes]
    return modifiers + [vk for vk in codes if vk not in _MODIFIER_ORDER]


def _app_behind(user32, own_pid: int, start_hwnd):
    """The next visible, titled application window below start_hwnd that is not Holo's."""
    import ctypes
    from ctypes import wintypes
    hwnd = user32.GetWindow(start_hwnd, 2)                       # GW_HWNDNEXT
    for _ in range(500):
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exstyle = user32.GetWindowLongW(hwnd, -20)               # GWL_EXSTYLE
        if (pid.value != own_pid and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
                and user32.GetWindowTextLengthW(hwnd) > 0 and not exstyle & 0x80   # WS_EX_TOOLWINDOW
                and not _is_cloaked(hwnd)):
            return hwnd
        hwnd = user32.GetWindow(hwnd, 2)
    return None


def _is_cloaked(hwnd) -> bool:
    """Hidden UWP/virtual-desktop windows report as visible but are 'cloaked'."""
    import ctypes
    try:
        cloaked = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
            ctypes.c_void_p(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return bool(cloaked.value)
    except Exception:
        return False


def _focus_away_from_holo(user32):
    """Keystrokes go to the active window. If that is Holo itself (the Desk page or a Test
    button), give the focus back to the app used before Holo first: keys sent to Holo's own
    window are never useful, and Alt combinations would put it into Windows' menu mode,
    which freezes the window until a click or Esc."""
    import ctypes
    from ctypes import wintypes
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    if pid.value != os.getpid():
        return None
    target = _app_behind(user32, os.getpid(), foreground)
    if not target:
        return None
    user32.SetForegroundWindow(target)
    for _ in range(25):                                          # up to ~0.25 s for the switch
        if user32.GetForegroundWindow() == target:
            break
        time.sleep(0.01)
    return foreground, target


def _return_focus_to_holo(user32, holo, target) -> None:
    """Puts Holo's window back in front after its keys reached the other app, so it does
    not look closed. The keyboard focus stays with that app (where the shortcut was meant
    to act, e.g. typing on after a language switch). Skipped when the shortcut itself moved
    the focus (alt+tab, win+d...). Changing the stacking order needs no special rights."""
    if user32.GetForegroundWindow() != target:
        return
    flags = 0x0001 | 0x0002 | 0x0010 | 0x0040        # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
    user32.SetWindowPos(holo, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST...
    user32.SetWindowPos(holo, -2, 0, 0, 0, 0, flags)  # ...then normal again, staying in front


def press_keys(spec: str) -> None:
    import ctypes
    codes = parse_keys(spec)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    handle = ctypes.c_void_p
    user32.GetForegroundWindow.restype = handle
    user32.GetWindow.restype, user32.GetWindow.argtypes = handle, [handle, ctypes.c_uint]
    for name in ("GetWindowThreadProcessId", "IsWindowVisible", "IsIconic", "GetWindowTextLengthW",
                 "SetForegroundWindow"):
        getattr(user32, name).argtypes = [handle] + ([ctypes.c_void_p] if name == "GetWindowThreadProcessId" else [])
    user32.GetWindowLongW.argtypes = [handle, ctypes.c_int]
    user32.SetWindowPos.argtypes = [handle, handle, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]
    moved = _focus_away_from_holo(user32)
    KEYUP, EXT = 0x0002, 0x0001
    # Short gaps between events: some apps miss combinations that arrive all at once.
    for vk in codes:
        user32.keybd_event(vk, 0, EXT if vk in _EXTENDED else 0, 0)
        time.sleep(0.01)
    for vk in reversed(codes):
        user32.keybd_event(vk, 0, (EXT if vk in _EXTENDED else 0) | KEYUP, 0)
        time.sleep(0.01)
    if moved:
        time.sleep(0.15)   # let the app handle the keys (e.g. a language switch) first
        _return_focus_to_holo(user32, *moved)


def check_action(action: ZoneAction) -> str | None:
    """A problem the user can fix in the action's text (shown live while typing), else None."""
    if action.kind == "pressKeys" and action.text.strip():
        try:
            parse_keys(action.text)
        except ActionError as exc:
            return str(exc)
    return None


def perform_async(action: ZoneAction, on_error, clipboard_setter=None) -> None:
    """Fire-and-forget wrapper; clipboard work must stay on the UI thread."""
    if action.kind == "copyText":
        try:
            perform(action, clipboard_setter)
        except ActionError as exc:
            on_error(str(exc))
        return

    def run():
        try:
            perform(action)
        except ActionError as exc:
            on_error(str(exc))
    threading.Thread(target=run, daemon=True).start()
