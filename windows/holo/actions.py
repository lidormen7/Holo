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
            _powershell(
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
                "$g=[System.Drawing.Graphics]::FromImage($bmp); "
                "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
                "[System.Windows.Forms.Clipboard]::SetImage($bmp)",
                sta=True,
            )
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
    return codes


def press_keys(spec: str) -> None:
    import ctypes
    codes = parse_keys(spec)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    KEYUP, EXT = 0x0002, 0x0001
    for vk in codes:
        user32.keybd_event(vk, 0, EXT if vk in _EXTENDED else 0, 0)
    for vk in reversed(codes):
        user32.keybd_event(vk, 0, (EXT if vk in _EXTENDED else 0) | KEYUP, 0)


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
