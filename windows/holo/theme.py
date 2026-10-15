# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Colors, fonts and light/dark handling for the Holo window.

The look follows the original Holo's direction: a quiet instrument with neutral
surfaces, one accent color, thin dividers and no decorative boxes, adapted to
Windows 11 (Segoe UI, Windows accent blues, optional Sun Valley ttk widgets).
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

IS_WINDOWS = sys.platform == "win32"

PALETTES = {
    "light": {
        "ground": "#F3F3F3", "surface": "#FFFFFF", "text": "#1B1B1B", "sub": "#5F5F5F",
        "faint": "#9A9A9A", "accent": "#0067C0", "accent_text": "#FFFFFF",
        "border": "#E5E5E5", "divider": "#EBEBEB", "rail": "#FBFBFB",
        "laptop": "#F6F6F6", "laptop_border": "#D6D6D6", "base": "#DDDDDD",
        "strip": "#F5F5F5", "nav_hover": "#EAEAEA", "nav_selected": "#E6E6E6",
        "ok": "#0F7B0F", "error": "#C42B1C", "field": "#FBFBFB",
    },
    "dark": {
        "ground": "#202020", "surface": "#2B2B2B", "text": "#FFFFFF", "sub": "#C5C5C5",
        "faint": "#8A8A8A", "accent": "#4CC2FF", "accent_text": "#000000",
        "border": "#3A3A3A", "divider": "#383838", "rail": "#303030",
        "laptop": "#323232", "laptop_border": "#4A4A4A", "base": "#474747",
        "strip": "#333333", "nav_hover": "#2D2D2D", "nav_selected": "#323232",
        "ok": "#6CCB5F", "error": "#FF99A4", "field": "#373737",
    },
}

FONT = ("Segoe UI", 10)
FONT_SEMI = ("Segoe UI Semibold", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 17)
FONT_HEAD = ("Segoe UI Semibold", 12)
FONT_MONO = ("Consolas", 10)


def blend(fg: str, bg: str, alpha: float) -> str:
    """fg drawn over bg at the given opacity, as a hex color (Tk has no alpha)."""
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[k] + (f[k] - b[k]) * alpha):02X}" for k in range(3))


def system_prefers_dark() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return False


def resolve(mode: str) -> str:
    """'system' | 'light' | 'dark' -> 'light' | 'dark'."""
    if mode == "system":
        return "dark" if system_prefers_dark() else "light"
    return "dark" if mode == "dark" else "light"


def apply_ttk(root: tk.Tk, name: str) -> None:
    """Styles the ttk widgets for the palette (Sun Valley theme when installed)."""
    pal = PALETTES[name]
    style = ttk.Style(root)
    try:
        import sv_ttk
        sv_ttk.set_theme(name, root)
    except Exception:
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(14, 6), background=pal["field"], foreground=pal["text"],
                        bordercolor=pal["border"], focuscolor=pal["accent"])
        style.map("TButton", background=[("active", pal["nav_hover"]), ("disabled", pal["surface"])],
                  foreground=[("disabled", pal["faint"])])
        style.configure("Accent.TButton", background=pal["accent"], foreground=pal["accent_text"])
        style.map("Accent.TButton", background=[("active", blend(pal["accent"], pal["surface"], 0.85)),
                                                ("disabled", pal["divider"])])
        style.configure("TEntry", fieldbackground=pal["field"], foreground=pal["text"],
                        bordercolor=pal["border"], insertcolor=pal["text"])
        style.configure("TCombobox", fieldbackground=pal["field"], foreground=pal["text"],
                        background=pal["field"], bordercolor=pal["border"], arrowcolor=pal["text"])
        style.map("TCombobox", fieldbackground=[("readonly", pal["field"])],
                  foreground=[("readonly", pal["text"])])
        style.configure("Horizontal.TProgressbar", background=pal["accent"], troughcolor=pal["divider"],
                        bordercolor=pal["divider"], lightcolor=pal["accent"], darkcolor=pal["accent"])
        style.configure("Horizontal.TScale", background=pal["accent"], troughcolor=pal["divider"])
    style.configure(".", background=pal["surface"])
    for widget in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TScale", "Horizontal.TScale"):
        style.configure(widget, background=pal["surface"])
    style.configure("TLabel", foreground=pal["text"], font=FONT)
    style.configure("TCheckbutton", foreground=pal["text"], font=FONT)
    style.configure("Sub.TLabel", foreground=pal["sub"], background=pal["surface"], font=FONT_SMALL)
    root.option_add("*TCombobox*Listbox.background", pal["field"])
    root.option_add("*TCombobox*Listbox.foreground", pal["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", pal["accent_text"])


def dark_title_bar(root: tk.Tk, dark: bool) -> None:
    """Asks Windows 10/11 to draw the window's title bar dark or light."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())  # type: ignore[attr-defined]
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def rounded_points(x0, y0, x1, y1, radii, steps=6):
    """Polygon points for a rectangle with per-corner radii (tl, tr, br, bl)."""
    import math
    tl, tr, br, bl = (min(r, (x1 - x0) / 2, (y1 - y0) / 2) for r in radii)
    pts = []

    def arc(cx, cy, r, start):
        if r <= 0:
            pts.extend([cx, cy])
            return
        for i in range(steps + 1):
            a = math.radians(start + 90 * i / steps)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])

    arc(x0 + tl, y0 + tl, tl, 180) if tl else pts.extend([x0, y0])
    arc(x1 - tr, y0 + tr, tr, 270) if tr else pts.extend([x1, y0])
    arc(x1 - br, y1 - br, br, 0) if br else pts.extend([x1, y1])
    arc(x0 + bl, y1 - bl, bl, 90) if bl else pts.extend([x0, y1])
    return pts
