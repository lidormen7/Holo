# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Holo for Windows: tkinter user interface (replaces the SwiftUI app)."""

from __future__ import annotations

import json
import queue
import statistics
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Callable, Optional

from . import actions as act
from . import background as bg
from . import theme
from .audio import AudioCaptureService, Observation, list_input_devices, default_input_name
from .core import (LAYOUTS, MAX_PATTERN_TAPS, PATTERN_NAMES, REJECTION_TEXT, SENSITIVITY_LOUDNESS,
                   SENSITIVITY_NAMES, LabeledTap, TapClassifier, TapPatternTracker, TrainingError,
                   ZoneLayout, leave_one_out)
from .storage import Profile, load_profile, save_profile, data_dir

EVAL_TAPS_PER_ZONE = 15
DESK_REFRACTORY_S = 0.03    # quiet time after a tap on the Desk (original: 0.14 s)
TRAY_REFRESH_MS = 60_000   # re-send the tray icon so a swapped image corrects itself
TRANSITION_SECONDS = 1.2
NEGATIVE_SECONDS = 12
MIN_CLEAN_AGREEMENT = 0.80

LAYOUT_CHOICES = {
    "1 zone · anywhere": 1,
    "2 zones · left / right": 2,
    "3 zones · left / right / front": 3,
    "4 zones · four corners": 4,
}

SENSITIVITY_HELP = {
    1: "Only firm, clear taps that closely match calibration run an action.",
    2: "Ignores most soft or uncertain sounds.",
    3: "The default balance between missed taps and false triggers.",
    4: "Lighter taps count; a little more prone to false triggers.",
    5: "Even light taps count; most prone to false triggers.",
}

DESK, CALIBRATE, ACTIONS, ACCURACY, SETTINGS = range(5)


# ----------------------------------------------------------------- widgets

class Skin:
    """Remembers which palette color each plain-tk widget option uses, for theme switches."""

    def __init__(self):
        self.items: list[tuple[tk.Misc, dict]] = []
        self.pal = theme.PALETTES["light"]

    def __call__(self, widget, **roles):
        self.items.append((widget, roles))
        self._apply(widget, roles)
        return widget

    def _apply(self, widget, roles):
        try:
            widget.configure(**{opt: self.pal[key] for opt, key in roles.items()})
        except tk.TclError:
            pass

    def repaint(self, pal):
        self.pal = pal
        alive = []
        for widget, roles in self.items:
            if widget.winfo_exists():
                self._apply(widget, roles)
                alive.append((widget, roles))
        self.items = alive


class Segmented(tk.Frame):
    """A row of mutually exclusive choices drawn like the Windows 11 segmented control."""

    def __init__(self, master, skin: Skin, options: list[str], command: Callable[[int], None], value=0):
        super().__init__(master, padx=3, pady=3)
        self.skin, self.command, self.value = skin, command, value
        self.labels = []
        for i, text in enumerate(options):
            lbl = tk.Label(self, text=text, padx=16, pady=5, font=theme.FONT, cursor="hand2")
            lbl.pack(side="left", padx=(0 if i == 0 else 2, 0))
            lbl.bind("<Button-1>", lambda e, i=i: self.set(i, notify=True))
            self.labels.append(lbl)
        self.paint()

    def set(self, value: int, notify=False):
        self.value = value
        self.paint()
        if notify:
            self.command(value)

    def paint(self):
        pal = self.skin.pal
        self.configure(bg=pal["ground"])
        for i, lbl in enumerate(self.labels):
            selected = i == self.value
            lbl.configure(bg=pal["surface"] if selected else pal["ground"],
                          fg=pal["text"] if selected else pal["sub"],
                          font=theme.FONT_SEMI if selected else theme.FONT)


class Nav:
    """Sidebar navigation. Mirrors the small part of ttk.Notebook's API the app uses."""

    def __init__(self, app: "HoloApp", top: tk.Frame, bottom: tk.Frame):
        self.app, self.top, self.bottom = app, top, bottom
        self.pages: list[tk.Frame] = []
        self.rows: list[tuple[tk.Frame, tk.Frame, tk.Label]] = []
        self.states: list[str] = []
        self.current = 0
        self.on_change: Optional[Callable[[], None]] = None

    def add(self, page: tk.Frame, text: str, bottom=False) -> int:
        index = len(self.pages)
        row = tk.Frame(self.bottom if bottom else self.top, height=38, cursor="hand2")
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        label = tk.Label(row, text=text, font=theme.FONT, anchor="w", padx=14)
        label.pack(fill="both", expand=True)
        bar = tk.Frame(row, width=3)
        bar.place(x=0, rely=0.5, y=-8, height=16)
        for w in (row, label):
            w.bind("<Button-1>", lambda e, i=index: self.click(i))
            w.bind("<Enter>", lambda e, i=index: self.paint(hover=i))
            w.bind("<Leave>", lambda e: self.paint())
        self.pages.append(page)
        self.rows.append((row, bar, label))
        self.states.append("normal")
        if index == 0:
            page.pack(fill="both", expand=True)
        return index

    def click(self, index):
        if self.states[index] != "disabled":
            self.select(index)

    def select(self, index: Optional[int] = None):
        if index is None:
            return self.current
        if index != self.current:
            self.pages[self.current].pack_forget()
            self.pages[index].pack(fill="both", expand=True)
            self.current = index
        self.paint()
        if self.on_change:
            self.on_change()

    def index(self, value):
        return value

    def tab(self, index, option=None, state=None):
        if state is not None:
            self.states[index] = state
            self.paint()
        if option == "state":
            return self.states[index]

    def paint(self, hover: Optional[int] = None):
        pal = self.app.skin.pal
        for i, (row, bar, label) in enumerate(self.rows):
            selected = i == self.current
            disabled = self.states[i] == "disabled"
            bg = pal["nav_selected"] if selected else pal["nav_hover"] if i == hover and not disabled \
                else pal["ground"]
            row.configure(bg=bg, cursor="arrow" if disabled else "hand2")
            label.configure(bg=bg, fg=pal["faint"] if disabled else pal["text"],
                            font=theme.FONT_SEMI if selected else theme.FONT)
            bar.configure(bg=pal["accent"] if selected else bg)


class DeskMap(tk.Canvas):
    """The desk around a laptop outline, in the original Holo's style: continuous side
    rails with thin dividers, a front area when the layout uses one, and a slim accent
    bar on the zone being targeted or just recognized."""

    GAP = 24

    def __init__(self, master, app: "HoloApp", layout: ZoneLayout, height=290):
        super().__init__(master, height=height, highlightthickness=0, bd=0)
        self.app = app
        self.listening = False
        self._timers: set[str] = set()
        self.set_layout(layout)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Destroy>", lambda e: self._cancel_timers())

    def set_layout(self, layout: ZoneLayout):
        self.layout = layout
        self._state = {z: "idle" for z in range(layout.count)}
        self._sub = {z: "" for z in range(layout.count)}
        self._draw()

    def _geometry(self):
        w, h = max(self.winfo_width(), 400), max(self.winfo_height(), 180)
        lap_w = min(240.0, w * 0.30)
        rail_w = (w - lap_w - 2 * self.GAP) / 2
        front_h = 66 if self.layout.has_front else 0
        lx0, lx1 = rail_w + self.GAP, rail_w + self.GAP + lap_w
        rx0 = lx1 + self.GAP
        r = 14
        boxes = {
            "L": ((0, 0, rail_w, h), (r, r, r, r)),
            "LR": ((0, 0, rail_w, h / 2), (r, r, 0, 0)),
            "LF": ((0, h / 2, rail_w, h), (0, 0, r, r)),
            "R": ((rx0, 0, w, h), (r, r, r, r)),
            "RR": ((rx0, 0, w, h / 2), (r, r, 0, 0)),
            "RF": ((rx0, h / 2, w, h), (0, 0, r, r)),
            "F": ((lx0, h - front_h, lx1, h), (r, r, r, r)),
        }
        laptop = (lx0, 0, lx1, h - front_h - (16 if front_h else 0))
        return w, h, boxes, laptop

    def _fill(self, state):
        pal = self.app.skin.pal
        return {
            "idle": pal["rail"],
            "done": pal["rail"],
            "target": theme.blend(pal["accent"], pal["surface"], 0.08),
            "active": theme.blend(pal["accent"], pal["surface"], 0.18),
            "reject": theme.blend(pal["error"], pal["surface"], 0.14),
        }[state]

    def _draw(self):
        pal = self.app.skin.pal
        self.configure(bg=pal["surface"])
        self.delete("all")
        w, h, boxes, laptop = self._geometry()
        used = {region: zone for zone, regions in enumerate(self.layout.regions) for region in regions}
        # Region fills first, then rail outlines and dividers so a split rail reads as one object.
        for region, zone in used.items():
            (x0, y0, x1, y1), radii = boxes[region]
            self.create_polygon(theme.rounded_points(x0, y0, x1, y1, radii),
                                fill=self._fill(self._state[zone]), outline="")
        for side in ("L", "R"):
            if any(k.startswith(side) for k in used):
                (x0, y0, x1, y1), radii = boxes[side]
                self.create_polygon(theme.rounded_points(x0, y0, x1, y1, radii), fill="",
                                    outline=pal["border"])
                if side + "R" in used:
                    self.create_line(x0 + 1, h / 2, x1 - 1, h / 2, fill=pal["divider"])
        if "F" in used:
            (x0, y0, x1, y1), radii = boxes["F"]
            self.create_polygon(theme.rounded_points(x0, y0, x1, y1, radii), fill="", outline=pal["border"])
        for region, zone in used.items():
            (x0, y0, x1, y1), radii = boxes[region]
            state = self._state[zone]
            if state == "target":
                self.create_polygon(theme.rounded_points(x0 + 1, y0 + 1, x1 - 1, y1 - 1, radii), fill="",
                                    outline=pal["accent"], width=2)
            self._label(region, zone, (x0, y0, x1, y1), state)
        self._laptop(*laptop)

    def _label(self, region, zone, box, state):
        pal = self.app.skin.pal
        x0, y0, x1, y1 = box
        cy = (y0 + y1) / 2
        name, sub = self.layout.names[zone], self._sub[zone]
        bar_color = pal["error"] if state == "reject" else pal["accent"]
        show_bar = state in ("active", "target", "reject")
        if region.startswith("L"):
            x, anchor, bar_x = x1 - 26, "e", x1 - 14
        elif region.startswith("R"):
            x, anchor, bar_x = x0 + 26, "w", x0 + 14
        else:
            x, anchor, bar_x = (x0 + x1) / 2, "center", None
        self.create_text(x, cy - (8 if sub else 0), text=name, anchor=anchor, fill=pal["text"],
                         font=theme.FONT_SEMI)
        if sub:
            self.create_text(x, cy + 10, text=sub, anchor=anchor, fill=pal["sub"], font=theme.FONT_SMALL)
        if not show_bar:
            return
        if bar_x is None:  # front area: horizontal bar along its bottom edge
            mid = (x0 + x1) / 2
            self.create_line(mid - 22, y1 - 7, mid + 22, y1 - 7, fill=bar_color, width=3, capstyle="round")
        else:
            half = 17 if state == "active" else 13
            self.create_line(bar_x, cy - half, bar_x, cy + half, fill=bar_color, width=3, capstyle="round")

    def _laptop(self, x0, y0, x1, y1):
        pal = self.app.skin.pal
        width = x1 - x0
        sh = min((y1 - y0) * 0.78, width * 0.72)
        sx0, sx1 = x0 + width * 0.06, x1 - width * 0.06
        sy1 = y1 - 12
        sy0 = sy1 - sh
        self.create_polygon(theme.rounded_points(sx0, sy0, sx1, sy1, (11, 11, 4, 4)), fill=pal["laptop"],
                            outline=pal["laptop_border"])
        self.create_line(x0 + 2, y1 - 5, x1 - 2, y1 - 5, fill=pal["base"], width=9, capstyle="round")
        cx, cy = (x0 + x1) / 2, (sy0 + sy1) / 2 - 8
        color = pal["accent"] if self.listening else pal["faint"]
        if self.listening:
            for dx, half in ((-16, 3), (-8, 9), (0, 14), (8, 7), (16, 2)):
                self.create_line(cx + dx, cy - half, cx + dx, cy + half, fill=color, width=2, capstyle="round")
        else:
            for dx in (-5, 5):
                self.create_line(cx + dx, cy - 9, cx + dx, cy + 9, fill=color, width=3, capstyle="round")
        self.create_text(cx, cy + 26, text="Laptop", fill=pal["sub"], font=theme.FONT_SMALL)

    def set_zone(self, zone: int, state: Optional[str] = None, sub: Optional[str] = None):
        if zone not in self._state:
            return
        if state is not None:
            self._state[zone] = state
        if sub is not None:
            self._sub[zone] = sub
        self._draw()

    def reset(self, sub_text: bool = True):
        for z in self._state:
            self._state[z] = "idle"
            if sub_text:
                self._sub[z] = ""
        self._draw()

    def flash(self, zone: int, state: str = "active", ms=450, then: str = "idle"):
        self.set_zone(zone, state)

        def restore():
            self._timers.discard(timer)
            self.set_zone(zone, then)
        timer = self.after(ms, restore)
        self._timers.add(timer)

    def _cancel_timers(self):
        for timer in self._timers:
            try:
                self.after_cancel(timer)
            except tk.TclError:
                pass
        self._timers.clear()


# --------------------------------------------------------------------- app

class HoloApp:
    def __init__(self, root: tk.Tk, start_hidden: bool = False, instance: Optional[bg.SingleInstance] = None):
        self.root = root
        self.instance = instance
        self.hidden = False
        self.skin = Skin()
        root.title("Holo")
        bg.apply_window_icon(root)
        root.geometry("1040x700")
        root.minsize(900, 640)

        self.events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.audio = AudioCaptureService(
            on_observation=lambda o: self.events.put(("obs", o)),
            on_error=lambda m: self.events.put(("error", m)),
        )
        self.profile: Profile = Profile()
        self.classifier: Optional[TapClassifier] = None
        self.mode = "desk"           # desk | calibrate | negatives | evaluate | idle
        self.armed_zone: Optional[int] = None
        self.ignore_until = 0.0
        self.cal_samples: list[LabeledTap] = []
        self.neg_samples: list[LabeledTap] = []
        self.neg_deadline = 0.0
        self.eval_records: list[tuple[int, Optional[int], float]] = []
        self.patterns = TapPatternTracker(self._pattern_can_grow)

        try:
            loaded = load_profile()
        except Exception as exc:
            loaded = None
            messagebox.showwarning("Holo", f"Saved profile could not be loaded:\n{exc}")
        if loaded:
            self.profile = loaded
            self._retrain()

        self.settings = self._load_settings()
        self.cal_layout: ZoneLayout = self.profile.layout   # layout for the next calibration
        self._apply_theme(build=True)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close_window)
        # Windows sign-out or shutdown: exit cleanly so the tray icon is removed first.
        root.protocol("WM_SAVE_YOURSELF", self._quit)
        self.tray = bg.TrayIcon(
            on_open=lambda: self.events.put(("call", self._show_window)),
            on_toggle_listen=lambda: self.events.put(("call", self._toggle_listening)),
            is_listening=lambda: self.audio.is_running,
            on_toggle_actions=lambda: self.events.put(("call", self._toggle_actions)),
            actions_enabled=lambda: bool(self.settings.get("actions_enabled", True)),
            on_toggle_autostart=lambda: self.events.put(("call", self._toggle_autostart)),
            on_exit=lambda: self.events.put(("call", self._quit)),
        )
        self._pump_id = root.after(30, self._pump)
        self._tick_id = root.after(100, self._tick_level)
        self._tray_id = root.after(TRAY_REFRESH_MS, self._tray_heartbeat)
        # Every PC and microphone sounds different, so nothing is recognized until
        # this exact microphone has been calibrated.
        self._apply_calibration_gate()
        if self.is_calibrated():
            self._start_listening()
        if start_hidden and self.tray.available and self.is_calibrated():
            self._hide_window(announce=False)

    # ------------------------------------------------------------------ theme
    @property
    def sensitivity(self) -> int:
        return int(self.settings.get("sensitivity", 3))

    def _apply_theme(self, build=False):
        name = theme.resolve(self.settings.get("appearance", "system"))
        self.theme_name = name
        pal = theme.PALETTES[name]
        theme.apply_ttk(self.root, name)
        self.root.configure(bg=pal["ground"])
        theme.dark_title_bar(self.root, name == "dark")
        if build:
            self.skin.pal = pal
            return
        self.skin.repaint(pal)
        for seg in self.segments:
            if seg.winfo_exists():
                seg.paint()
        self.nav.paint()
        for m in (self.desk_map, self.cal_map, self.eval_map):
            m._draw()
        self.eval_report.configure(bg=pal["surface"], fg=pal["text"], insertbackground=pal["text"])
        self._paint_status()

    # ------------------------------------------------------------------ layout
    def _build(self):
        s = self.skin
        self.segments: list[Segmented] = []
        sidebar = s(tk.Frame(self.root, width=220, padx=10, pady=18), bg="ground")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = s(tk.Frame(sidebar, padx=12, pady=4), bg="ground")
        brand.pack(fill="x", pady=(0, 16))
        try:
            self._brand_icon = tk.PhotoImage(file=str(bg.ICON_PNG)).subsample(10)
            s(tk.Label(brand, image=self._brand_icon), bg="ground").pack(side="left")
        except tk.TclError:
            pass
        s(tk.Label(brand, text="Holo", font=("Segoe UI Semibold", 12), padx=8), bg="ground", fg="text"
          ).pack(side="left")
        top = s(tk.Frame(sidebar), bg="ground")
        top.pack(fill="x")
        status = s(tk.Frame(sidebar, padx=12, pady=8), bg="ground")
        status.pack(side="bottom", fill="x")
        self.status_dot = s(tk.Canvas(status, width=8, height=8, highlightthickness=0), bg="ground")
        self.status_dot.pack(side="left", padx=(0, 8))
        self.status_text = tk.StringVar(value="Paused")
        s(tk.Label(status, textvariable=self.status_text, font=theme.FONT_SMALL, anchor="w", wraplength=170,
                   justify="left"), bg="ground", fg="sub").pack(side="left", fill="x")
        bottom = s(tk.Frame(sidebar), bg="ground")
        bottom.pack(side="bottom", fill="x")

        card = s(tk.Frame(self.root, highlightthickness=1), bg="surface", highlightbackground="border",
                 highlightcolor="border")
        card.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
        self.status = tk.StringVar(value="Ready.")
        s(tk.Label(card, textvariable=self.status, font=theme.FONT_SMALL, anchor="w", padx=34, pady=8),
          bg="surface", fg="faint").pack(side="bottom", fill="x")
        content = s(tk.Frame(card, padx=34, pady=24), bg="surface")
        content.pack(fill="both", expand=True)
        self.nav = Nav(self, top, bottom)
        self.tabs = self.nav  # the gate and tests address pages through this

        self._build_desk(content)
        self._build_calibration(content)
        self._build_actions(content)
        self._build_evaluation(content)
        self._build_settings(content)
        self.nav.on_change = self._tab_changed
        self.nav.paint()
        self.eval_report.configure(bg=s.pal["surface"], fg=s.pal["text"], insertbackground=s.pal["text"])
        self._paint_status()

    def _page(self, parent, title: str, bottom=False):
        s = self.skin
        page = s(tk.Frame(parent), bg="surface")
        header = s(tk.Frame(page), bg="surface")
        header.pack(fill="x", pady=(0, 18))
        s(tk.Label(header, text=title, font=theme.FONT_TITLE), bg="surface", fg="text").pack(side="left")
        right = s(tk.Frame(header), bg="surface")
        right.pack(side="right")
        self.nav.add(page, title, bottom=bottom)
        return page, right

    def _label(self, parent, text="", role="text", font=theme.FONT, **kw):
        lbl = tk.Label(parent, text=text, font=font, anchor=kw.pop("anchor", "w"),
                       justify=kw.pop("justify", "left"), **kw)
        return self.skin(lbl, bg="surface", fg=role)

    def _divider(self, parent, pady=0):
        line = self.skin(tk.Frame(parent, height=1), bg="divider")
        line.pack(fill="x", pady=pady)
        return line

    def _build_desk(self, parent):
        page, right = self._page(parent, "Desk")
        self.desk_profile_text = tk.StringVar()
        self._label(right, textvariable=self.desk_profile_text, role="sub", font=theme.FONT_SMALL).pack(side="left")
        self.desk_map = DeskMap(page, self, self.profile.layout)
        self.desk_map.pack(fill="x")
        strip = self.skin(tk.Frame(page, padx=18, pady=12), bg="strip")
        strip.pack(fill="x", pady=(20, 14))
        self.desk_result = tk.StringVar(value="Waiting for a tap")
        self.desk_meta = tk.StringVar(value="")
        self.skin(tk.Label(strip, textvariable=self.desk_result, font=theme.FONT_SEMI, anchor="w"),
                  bg="strip", fg="text").pack(side="left")
        self.skin(tk.Label(strip, textvariable=self.desk_meta, font=theme.FONT_SMALL, anchor="e"),
                  bg="strip", fg="sub").pack(side="right")
        row = self.skin(tk.Frame(page), bg="surface")
        row.pack(fill="x")
        self.actions_enabled = tk.BooleanVar(value=self.settings.get("actions_enabled", True))
        self.actions_enabled.trace_add("write", lambda *_: self._save_setting(
            "actions_enabled", self.actions_enabled.get()))
        ttk.Checkbutton(row, text="Run actions when a tap is recognized", variable=self.actions_enabled
                        ).pack(side="left")
        self._label(row, "Closing the window keeps Holo running next to the clock.", role="sub",
                    font=theme.FONT_SMALL).pack(side="right")
        self.desk_hint = tk.StringVar()
        self._label(page, textvariable=self.desk_hint, role="sub", font=theme.FONT_SMALL, wraplength=700
                    ).pack(fill="x", pady=(14, 0))
        self._update_desk_hint()

    def _build_calibration(self, parent):
        page, right = self._page(parent, "Calibrate")
        self._label(right, "Zones", role="sub", font=theme.FONT_SMALL).pack(side="left", padx=(0, 8))
        self.layout_var = tk.StringVar(value=next(k for k, v in LAYOUT_CHOICES.items()
                                                  if v == self.cal_layout.count))
        self.layout_box = ttk.Combobox(right, textvariable=self.layout_var, state="readonly", width=28,
                                       values=list(LAYOUT_CHOICES))
        self.layout_box.pack(side="left")
        self.layout_box.bind("<<ComboboxSelected>>", lambda e: self._layout_selected())
        self.cal_map = DeskMap(page, self, self.cal_layout, height=250)
        self.cal_map.pack(fill="x")
        self.cal_title = tk.StringVar(value="Teach Holo your desk")
        self.cal_msg = tk.StringVar(value=(
            "Put the laptop where it will stay, keep the room quiet, then press Begin. "
            "Tap in each highlighted zone until its counter is full."))
        self._label(page, textvariable=self.cal_title, font=theme.FONT_HEAD).pack(fill="x", pady=(18, 2))
        self._label(page, textvariable=self.cal_msg, role="sub", wraplength=700).pack(fill="x")
        self.cal_progress = ttk.Progressbar(page, maximum=1.0)
        self.cal_progress.pack(fill="x", pady=(10, 14))
        row = self.skin(tk.Frame(page), bg="surface")
        row.pack(fill="x")
        self.cal_begin = ttk.Button(row, text="Begin calibration", style="Accent.TButton", command=self._cal_begin)
        self.cal_undo = ttk.Button(row, text="Undo last", command=self._cal_undo, state="disabled")
        self.cal_redo = ttk.Button(row, text="Redo zone", command=self._cal_redo_zone, state="disabled")
        self.cal_save = ttk.Button(row, text="Save profile", style="Accent.TButton", command=self._cal_save,
                                   state="disabled")
        self.cal_cancel = ttk.Button(row, text="Cancel", command=self._cal_cancel, state="disabled")
        for b in (self.cal_begin, self.cal_undo, self.cal_redo):
            b.pack(side="left", padx=(0, 8))
        self.cal_save.pack(side="right")
        self.cal_cancel.pack(side="right", padx=(0, 8))
        self._divider(page, pady=16)
        neg = self.skin(tk.Frame(page), bg="surface")
        neg.pack(fill="x")
        text = self.skin(tk.Frame(neg), bg="surface")
        text.pack(side="left", fill="x", expand=True)
        self._label(text, "Non-tap sounds", font=theme.FONT_SEMI).pack(fill="x")
        self._label(text, "After saving, record talking, typing and touching the laptop so they never trigger "
                          "an action. Strongly recommended with 1 zone.", role="sub", font=theme.FONT_SMALL,
                    wraplength=480).pack(fill="x")
        ttk.Button(neg, text="Clear", command=self._neg_clear).pack(side="right")
        self.neg_btn = ttk.Button(neg, text=f"Record for {NEGATIVE_SECONDS} s", command=self._neg_begin)
        self.neg_btn.pack(side="right", padx=8)

    def _build_actions(self, parent):
        page, _ = self._page(parent, "Actions")
        self.action_zone = tk.IntVar(value=0)
        self.action_zone_row = self.skin(tk.Frame(page), bg="surface")
        self.action_zone_row.pack(fill="x", pady=(0, 6))
        grid = self.skin(tk.Frame(page), bg="surface")
        grid.pack(fill="x")
        labels = list(act.ACTION_KINDS.values())
        self._suppress_action_save = False
        self.action_rows = []
        for pattern in range(MAX_PATTERN_TAPS):
            r = pattern * 3
            self._label(grid, PATTERN_NAMES[pattern], font=theme.FONT_SEMI, width=12).grid(
                row=r, column=0, sticky="w", pady=(14, 0))
            kind_var = tk.StringVar(value=labels[0])
            text_var = tk.StringVar()
            ttk.Combobox(grid, values=labels, textvariable=kind_var, state="readonly", width=24).grid(
                row=r, column=1, padx=(0, 12), pady=(14, 0), sticky="w")
            entry = ttk.Entry(grid, textvariable=text_var, width=30)
            entry.grid(row=r, column=2, pady=(14, 0), sticky="ew")
            browse = ttk.Button(grid, text="Browse…", command=lambda p=pattern: self._browse(p))
            browse.grid(row=r, column=3, padx=(8, 0), pady=(14, 0))
            ttk.Button(grid, text="Test", command=lambda p=pattern: self._test_action(p)).grid(
                row=r, column=4, padx=(8, 0), pady=(14, 0))
            hint = self._label(grid, role="sub", font=theme.FONT_SMALL, wraplength=420)
            hint.grid(row=r + 1, column=2, columnspan=3, sticky="w", pady=(4, 12))
            if pattern < MAX_PATTERN_TAPS - 1:
                self.skin(tk.Frame(grid, height=1), bg="divider").grid(row=r + 2, column=0, columnspan=5,
                                                                         sticky="ew")
            kind_var.trace_add("write", lambda *_, p=pattern: self._action_edited(p))
            text_var.trace_add("write", lambda *_, p=pattern: self._action_edited(p))
            self.action_rows.append((kind_var, text_var, entry, browse, hint))
        grid.columnconfigure(2, weight=1)
        self._label(page, wraplength=700, role="sub", font=theme.FONT_SMALL, text=(
            "Double and triple taps: tap the same zone again within about half a second. If a zone has "
            "only a single-tap action it runs immediately; otherwise Holo waits a moment for more taps. "
            "Actions run on the Desk page and while Holo runs in the background, only for confidently "
            "recognized taps. Commands run every time, so make them safe to repeat.")).pack(fill="x", pady=(18, 0))
        self._rebuild_action_zones()

    def _build_evaluation(self, parent):
        page, right = self._page(parent, "Accuracy test")
        self.eval_cancel = ttk.Button(right, text="Cancel", command=self._eval_cancel, state="disabled")
        self.eval_cancel.pack(side="right")
        self.eval_begin = ttk.Button(right, text="Begin test", style="Accent.TButton", command=self._eval_begin)
        self.eval_begin.pack(side="right", padx=(0, 8))
        self.eval_map = DeskMap(page, self, self.profile.layout, height=230)
        self.eval_map.pack(fill="x")
        self.eval_msg = tk.StringVar(value=(
            f"Measures real accuracy with new single taps: {EVAL_TAPS_PER_ZONE} per zone. "
            "Rejected taps count as wrong. Actions do not run during the test."))
        self._label(page, textvariable=self.eval_msg, wraplength=700).pack(fill="x", pady=(16, 8))
        self.eval_report = tk.Text(page, height=9, font=theme.FONT_MONO, relief="flat", bd=0,
                                   highlightthickness=0)
        self.eval_report.pack(fill="both", expand=True)
        self.eval_report.configure(state="disabled")

    def _build_settings(self, parent):
        page, _ = self._page(parent, "Settings", bottom=True)

        def section(title, detail):
            row = self.skin(tk.Frame(page, pady=12), bg="surface")
            row.pack(fill="x")
            text = self.skin(tk.Frame(row), bg="surface")
            text.pack(side="left", fill="x", expand=True)
            self._label(text, title, font=theme.FONT_SEMI).pack(fill="x")
            if detail:
                self._label(text, detail, role="sub", font=theme.FONT_SMALL, wraplength=420).pack(fill="x")
            return row

        head = section("Tap sensitivity", "How sure Holo must be before it reacts. Stricter settings ignore "
                                          "soft taps and anything that does not closely match your calibration.")
        seg = Segmented(page, self.skin, [SENSITIVITY_NAMES[i] for i in range(1, 6)], value=self.sensitivity - 1,
                        command=lambda i: self._set_sensitivity(i + 1))
        seg.pack(fill="x")
        for lbl in seg.labels:
            lbl.pack_configure(fill="x", expand=True)
        self.segments.append(seg)
        self.sens_help = tk.StringVar(value=SENSITIVITY_HELP[self.sensitivity])
        self._label(page, textvariable=self.sens_help, role="sub", font=theme.FONT_SMALL).pack(fill="x", pady=(6, 12))
        self._divider(page)

        row = section("Appearance", "Follow Windows, or choose light or dark.")
        modes = ["system", "light", "dark"]
        seg = Segmented(row, self.skin, ["System", "Light", "Dark"],
                        value=modes.index(self.settings.get("appearance", "system")),
                        command=lambda i: self._set_appearance(modes[i]))
        seg.pack(side="right")
        self.segments.append(seg)
        self._divider(page)

        row = section("Microphone", "Changing it needs a new calibration.")
        side = self.skin(tk.Frame(row), bg="surface")
        side.pack(side="right")
        self.device_var = tk.StringVar(value=self.profile.input_device or default_input_name() or "")
        self.device_box = ttk.Combobox(side, textvariable=self.device_var, width=40, state="readonly")
        self.device_box.pack(fill="x")
        self.device_box.bind("<<ComboboxSelected>>", lambda e: self._device_changed())
        meter = self.skin(tk.Frame(side), bg="surface")
        meter.pack(fill="x", pady=(8, 0))
        self.listen_btn = ttk.Button(meter, text="Start listening", command=self._toggle_listening)
        self.listen_btn.pack(side="right")
        self.level = ttk.Progressbar(meter, maximum=1.0)
        self.level.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._refresh_devices()
        self._divider(page)

        row = section("Start with Windows", "Runs in the background next to the clock.")
        self.autostart_var = tk.BooleanVar(value=bg.autostart_enabled())
        ttk.Checkbutton(row, variable=self.autostart_var, command=self._autostart_clicked).pack(side="right")
        self._divider(page)
        row = section("Run actions", "Turn off to only show recognized taps.")
        ttk.Checkbutton(row, variable=self.actions_enabled).pack(side="right")

    # --------------------------------------------------------- settings
    def _set_sensitivity(self, value: int):
        self._save_setting("sensitivity", value)
        self.sens_help.set(SENSITIVITY_HELP[value])
        self._apply_detector_loudness()

    def _apply_detector_loudness(self):
        # Guided capture always listens at the balanced level, one tap at a time, so
        # calibration stays comparable. The Desk hears quick follow-up taps for
        # double/triple patterns.
        desk = self.mode == "desk"
        self.audio.configure_detector(
            loudness=SENSITIVITY_LOUDNESS[self.sensitivity if desk else 3],
            refractory_after_tap=DESK_REFRACTORY_S if desk else 0.14,
            pattern_followup=desk and self.patterns.pending is not None,
        )

    def _set_appearance(self, mode: str):
        self._save_setting("appearance", mode)
        self._apply_theme()

    # ----------------------------------------------------------- listening
    def _refresh_devices(self):
        try:
            names = [label for _, label in list_input_devices()]
        except Exception as exc:
            names = []
            self._set_status(f"Could not list microphones: {exc}")
        self.device_box["values"] = names
        if self.device_var.get() not in names and names:
            self.device_var.set(default_input_name() if default_input_name() in names else names[0])

    def _device_changed(self):
        if self.audio.is_running:
            self._start_listening()
        self._apply_calibration_gate()

    # --------------------------------------------------------- calibration gate
    def calibration_problem(self) -> Optional[str]:
        """Why the current microphone cannot be used yet, or None if it is calibrated."""
        if self.classifier is None:
            return ("Every PC and microphone hears desk taps differently, so Holo has to learn your "
                    "setup first.")
        if (self.profile.input_device or "") != (self.device_var.get() or ""):
            return (f"The selected microphone is not the one this profile was calibrated with "
                    f"({self.profile.input_device or 'unknown'}). Calibrate again for this microphone.")
        if self.audio.is_running and abs(self.audio.sample_rate - self.profile.sample_rate) > 1:
            return (f"This microphone now runs at {int(self.audio.sample_rate)} Hz instead of the "
                    f"calibrated {int(self.profile.sample_rate)} Hz. Calibrate again.")
        return None

    def is_calibrated(self) -> bool:
        return self.calibration_problem() is None

    def _apply_calibration_gate(self):
        problem = self.calibration_problem()
        locked = "disabled" if problem else "normal"
        self.tabs.tab(DESK, state=locked)
        self.tabs.tab(ACCURACY, state=locked)
        self._update_desk_hint()
        if problem and self.mode not in ("calibrate", "evaluate", "negatives"):
            self.tabs.select(CALIBRATE)
            self.mode = "idle"
            self.cal_title.set("Welcome to Holo" if self.classifier is None else "Calibration needed")
            self.cal_msg.set(problem + " Put the laptop where it will stay, keep the room quiet, "
                             "choose the number of zones and press Begin calibration. The Desk page "
                             "unlocks when you save.")

    def _toggle_listening(self):
        if self.audio.is_running:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> bool:
        try:
            self.audio.start(self.device_var.get() or None)
        except Exception as exc:
            messagebox.showerror("Holo", f"Could not open the microphone:\n{exc}\n\n"
                                 "Check Settings → Privacy & security → Microphone and allow desktop apps.")
            self._update_listen_btn()
            return False
        self._apply_detector_loudness()
        self._update_listen_btn()
        self.ignore_until = time.monotonic() + 0.8  # detector's room-learning period
        self._set_status(f"Listening on {self.audio.device_label} at {int(self.audio.sample_rate)} Hz.")
        self._apply_calibration_gate()
        return True

    def _stop_listening(self):
        self.audio.stop()
        self._update_listen_btn()
        self._set_status("Listening paused.")
        if self.mode in ("calibrate", "evaluate", "negatives"):
            self.armed_zone = None

    def _update_listen_btn(self):
        self.listen_btn.configure(text="Pause listening" if self.audio.is_running else "Start listening")
        self._paint_status()
        for m in (self.desk_map, self.cal_map, self.eval_map):
            m.listening = self.audio.is_running
            m._draw()
        if hasattr(self, "tray"):
            self.tray.refresh()

    def _paint_status(self):
        pal = self.skin.pal
        running = self.audio.is_running
        mic = (self.audio.device_label or self.device_var.get() or "").split("  [")[0]
        self.status_text.set(f"Listening · {mic}" if running else "Paused")
        self.status_dot.delete("all")
        self.status_dot.create_oval(0, 0, 7, 7, fill=pal["ok"] if running else pal["faint"], outline="")

    def _tick_level(self):
        self.level["value"] = min(self.audio.level * 20, 1.0) if self.audio.is_running else 0
        if self.mode == "negatives":
            left = self.neg_deadline - time.monotonic()
            if left <= 0:
                self._neg_finish()
            else:
                self.cal_title.set("Recording non-tap sounds")
                self.cal_msg.set(f"Talk, type and touch the laptop normally… {left:.0f} s left "
                                 f"({len(self.neg_samples)} captured)")
        if self.instance is not None and self.instance.show_requested():
            self._show_window()  # someone launched Holo again while it was running
        self._tick_id = self.root.after(100, self._tick_level)

    def _pump(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "obs":
                    self._handle(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self._set_status(str(payload))
                    if self.hidden:
                        self.tray.notify(str(payload))
                elif kind == "call":
                    payload()  # type: ignore[operator]
        except queue.Empty:
            pass
        fired = self.patterns.poll(time.perf_counter())
        for zone, count in fired:
            self._fire_pattern(zone, count)
        if fired:
            self._apply_detector_loudness()
        self._pump_id = self.root.after(15, self._pump)

    def _handle(self, obs: Observation):
        if time.monotonic() < self.ignore_until:
            return
        if self.mode == "calibrate":
            self._cal_observe(obs)
        elif self.mode == "negatives":
            self.neg_samples.append(LabeledTap(None, obs.feature, negative_label="background"))
        elif self.mode == "evaluate":
            self._eval_observe(obs)
        elif self.mode == "desk":
            self._desk_observe(obs)

    # ----------------------------------------------------------------- desk
    def _tab_changed(self):
        index = self.tabs.index(self.tabs.select())
        if self.mode not in ("calibrate", "evaluate", "negatives"):
            # In the background the Desk behaviour runs no matter which page was last open.
            self.mode = "desk" if index == DESK or self.hidden else "idle"
            if self.mode != "desk":
                self.patterns.reset()
        self._apply_detector_loudness()

    def _update_desk_hint(self):
        if not hasattr(self, "device_var") or not self.is_calibrated():
            self.desk_hint.set("Calibration required. Open Calibrate to teach Holo your desk.")
            self.desk_profile_text.set("")
        else:
            acc = self.profile.loo_accuracy
            extra = f" Calibration agreement {acc:.0%}." if acc is not None else ""
            self.desk_hint.set(f"{len(self.profile.positives)} calibration taps, {len(self.profile.negatives)} "
                               f"non-tap sounds.{extra} Moving the laptop changes the sound path, so "
                               "recalibrate if you do.")
            n = self.profile.zone_count
            self.desk_profile_text.set(f"{n} zone{'s' if n > 1 else ''} · {self.profile.name}")

    def _desk_observe(self, obs: Observation):
        if not self.is_calibrated():
            return
        decision = self.classifier.predict(obs.feature, self.sensitivity)
        latency = (time.perf_counter() - obs.detected_at) * 1000
        z = decision.zone if decision.accepted else self._pattern_continuation(decision, obs)
        if z is None:
            self.desk_result.set(f"Ignored: {REJECTION_TEXT.get(decision.rejection, decision.rejection)}")
            self.desk_meta.set("")
            return
        self.desk_map.flash(z, "active", 300)
        for zone, count in self.patterns.add(z, obs.detected_at):
            self._fire_pattern(zone, count, latency, decision.confidence)
        pending = self.patterns.pending
        if pending:
            name = self.profile.layout.names[pending[0]]
            self.desk_result.set(f"{name} · {pending[1]} tap{'s' if pending[1] > 1 else ''}…")
            self.desk_meta.set("")
        self._apply_detector_loudness()

    def _pattern_continuation(self, decision, obs: Observation) -> Optional[int]:
        """A quick follow-up tap often overlaps the previous tap's ringing, so it can sound
        unlike the calibration taps. While a pattern that began with a confidently
        recognised tap is waiting, such a tap continues it unless it was clipped or is
        nearest to a different zone."""
        pending = self.patterns.pending
        if pending is None or decision.rejection in ("clippedSignal", "schemaMismatch"):
            return None
        if obs.detected_at - self.patterns.last_tap_at > self.patterns.gap:
            return None
        finite = [(d, i) for i, d in enumerate(decision.distances) if d != float("inf")]
        if finite and min(finite)[1] != pending[0]:
            return None
        return pending[0]

    def _pattern_can_grow(self, zone: int, taps: int) -> bool:
        """True when a longer tap pattern on this zone has an action worth waiting for."""
        return any(a.kind != "none" for a in self.profile.actions[zone][taps:])

    def _fire_pattern(self, zone: int, count: int, latency: Optional[float] = None,
                      confidence: Optional[float] = None):
        action = self.profile.actions[zone][count - 1]
        label = f"{self.profile.layout.names[zone]} · {PATTERN_NAMES[count - 1]}"
        if self.actions_enabled.get() and action.kind != "none":
            act.perform_async(action, lambda m: self.events.put(("error", m)), self._set_clipboard)
            label += f"  →  {act.ACTION_KINDS[action.kind]}"
        self.desk_result.set(label)
        meta = []
        if confidence is not None:
            meta.append(f"{confidence:.0%}")
        if latency is not None:
            meta.append(f"{latency:.0f} ms")
        self.desk_meta.set(" · ".join(meta))

    def _set_clipboard(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    # ---------------------------------------------------------- calibration
    def _ensure_listening(self) -> bool:
        return self.audio.is_running or self._start_listening()

    def _guided_busy(self) -> bool:
        if self.mode in ("calibrate", "evaluate", "negatives"):
            messagebox.showinfo("Holo", "Finish or cancel the current guided capture first.")
            return True
        return False

    def _layout_selected(self):
        if self.mode in ("calibrate", "negatives"):
            self.layout_var.set(next(k for k, v in LAYOUT_CHOICES.items() if v == self.cal_layout.count))
            messagebox.showinfo("Holo", "Cancel the current calibration before changing the number of zones.")
            return
        self.cal_layout = LAYOUTS[LAYOUT_CHOICES[self.layout_var.get()]]
        self.cal_map.set_layout(self.cal_layout)
        self._cal_buttons_idle()
        self.cal_progress["value"] = 0
        n = self.cal_layout.count
        self.cal_title.set(f"{n} zone{'s' if n > 1 else ''} · {self.cal_layout.taps} taps each")
        if n != self.profile.zone_count and self.classifier is not None:
            self.cal_msg.set(f"Press Begin calibration. Your current {self.profile.zone_count}-zone profile "
                             "stays active until you save.")
        else:
            self.cal_msg.set("Press Begin calibration when the room is quiet.")

    def _cal_begin(self):
        if self._guided_busy() or not self._ensure_listening():
            return
        self.mode = "calibrate"
        self._apply_detector_loudness()
        self.cal_samples = []
        self.cal_begin.configure(state="disabled")
        self.cal_save.configure(state="disabled")
        self.layout_box.configure(state="disabled")
        for b in (self.cal_undo, self.cal_redo, self.cal_cancel):
            b.configure(state="normal")
        self._cal_arm_next()

    def _cal_count(self, z):
        return sum(1 for s in self.cal_samples if s.zone == z)

    def _cal_target(self, z):
        return self.cal_layout.taps_per_zone(z)

    def _cal_refresh_map(self):
        for z in range(self.cal_layout.count):
            done = self._cal_count(z) >= self._cal_target(z)
            state = "target" if z == self.armed_zone else "done" if done else "idle"
            self.cal_map.set_zone(z, state, f"{self._cal_count(z)} / {self._cal_target(z)}")
        total = sum(self._cal_target(z) for z in range(self.cal_layout.count))
        self.cal_progress["value"] = len(self.cal_samples) / max(total, 1)

    def _cal_arm_next(self, delay=0.0):
        nxt = next((z for z in range(self.cal_layout.count) if self._cal_count(z) < self._cal_target(z)), None)
        self.armed_zone = nxt
        self.ignore_until = time.monotonic() + delay
        self._cal_refresh_map()
        if nxt is None:
            self._cal_complete()
            return
        left = self._cal_target(nxt) - self._cal_count(nxt)
        self.cal_title.set(("Move to the next zone… " if delay else "") + self.cal_layout.instruction(nxt))
        self.cal_msg.set(f"{left} more single tap{'s' if left > 1 else ''}. Spread them across the area and "
                         "pause briefly between taps.")

    def _cal_observe(self, obs: Observation):
        z = self.armed_zone
        if z is None:
            return
        issue = obs.feature.quality.guided_issue()
        if issue:
            self.cal_msg.set(f"{issue}  ({self.cal_layout.names[z]}: {self._cal_count(z)} / {self._cal_target(z)})")
            self.cal_map.flash(z, "reject", 260, then="target")
            return
        self.cal_samples.append(LabeledTap(z, obs.feature))
        done = self._cal_count(z) >= self._cal_target(z)
        self._cal_arm_next(delay=TRANSITION_SECONDS if done else 0.0)

    def _cal_undo(self):
        if self.cal_samples:
            self.cal_samples.pop()
            self.cal_save.configure(state="disabled")
            self.mode = "calibrate"
            self._cal_arm_next()

    def _cal_redo_zone(self):
        z = self.armed_zone if self.armed_zone is not None else (
            self.cal_samples[-1].zone if self.cal_samples else None)
        if z is None:
            return
        self.cal_samples = [s for s in self.cal_samples if s.zone != z]
        self.cal_save.configure(state="disabled")
        self.mode = "calibrate"
        self._cal_arm_next()

    def _cal_cancel(self):
        self.mode = "idle"
        self.armed_zone = None
        self.cal_samples = []
        self.cal_map.reset()
        self.cal_progress["value"] = 0
        self.cal_title.set("Calibration cancelled")
        self.cal_msg.set("Your previous profile is unchanged.")
        self._cal_buttons_idle()

    def _cal_buttons_idle(self):
        self.cal_begin.configure(state="normal")
        self.layout_box.configure(state="readonly")
        for b in (self.cal_undo, self.cal_redo, self.cal_cancel, self.cal_save):
            b.configure(state="disabled")

    def _cal_complete(self):
        self.mode = "idle"
        self.cal_title.set("Checking calibration consistency…")
        self.root.update_idletasks()
        try:
            acc, per_zone = leave_one_out(self.cal_samples, self.cal_layout.count)
        except TrainingError as exc:
            self.cal_title.set("Calibration failed")
            self.cal_msg.set(str(exc))
            return
        self._cal_loo = acc
        zones = range(self.cal_layout.count)
        weakest = min(zones, key=lambda z: per_zone[z])
        for z in zones:
            self.cal_map.set_zone(z, "idle", f"{per_zone[z]:.0%} agreement")
        good = acc >= MIN_CLEAN_AGREEMENT
        self.cal_title.set(f"All {len(self.cal_samples)} taps captured · {acc:.0%} agreement")
        msg = ("Looks consistent. Save the profile, then assign actions." if good else
               f"Weak. Consider Redo zone for {self.cal_layout.names[weakest]} before saving.")
        if self.cal_layout.count == 1:
            msg += " With one zone any tap-like sound can trigger, so record non-tap sounds after saving."
        self.cal_msg.set(msg)
        self.armed_zone = None if good else weakest
        if not good:
            self.cal_map.set_zone(weakest, "target")
        self.cal_save.configure(state="normal")
        self.cal_undo.configure(state="normal")
        self.cal_redo.configure(state="disabled" if good else "normal")

    def _cal_save(self):
        self.profile.positives = list(self.cal_samples)
        self.profile.negatives = []
        self.profile.loo_accuracy = getattr(self, "_cal_loo", None)
        self.profile.input_device = self.device_var.get() or None
        self.profile.sample_rate = self.audio.sample_rate
        self.profile.zone_count = self.cal_layout.count
        self.profile.resize_actions(self.cal_layout.count)
        self.desk_map.set_layout(self.profile.layout)
        self.eval_map.set_layout(self.profile.layout)
        self._rebuild_action_zones()
        self.patterns.reset()
        self._retrain()
        self._persist()
        self.mode = "idle"
        self.armed_zone = None
        self._cal_buttons_idle()
        self.cal_map.reset()
        self.cal_progress["value"] = 0
        self.cal_title.set("Profile saved")
        self.cal_msg.set("Assign actions on the Actions page, then use the Desk page.")
        self._apply_calibration_gate()

    def _neg_begin(self):
        if not self.is_calibrated():
            messagebox.showinfo("Holo", "Calibrate and save a profile first.")
            return
        if self._guided_busy() or not self._ensure_listening():
            return
        self.neg_samples = []
        self.mode = "negatives"
        self._apply_detector_loudness()
        self.neg_deadline = time.monotonic() + NEGATIVE_SECONDS

    def _neg_finish(self):
        self.mode = "idle"
        self.profile.negatives.extend(self.neg_samples)
        self._retrain()
        self._persist()
        self.cal_title.set("Non-tap sounds recorded")
        self.cal_msg.set(f"Added {len(self.neg_samples)} sounds ({len(self.profile.negatives)} total).")
        self._update_desk_hint()
        self._tab_changed()

    def _neg_clear(self):
        self.profile.negatives = []
        self._retrain()
        self._persist()
        self._update_desk_hint()
        self.cal_title.set("Non-tap sounds cleared")
        self.cal_msg.set("")

    # ----------------------------------------------------------- evaluation
    def _eval_begin(self):
        if not self.is_calibrated():
            messagebox.showinfo("Holo", "Calibrate and save a profile first.")
            return
        if self._guided_busy() or not self._ensure_listening():
            return
        self.mode = "evaluate"
        self._apply_detector_loudness()
        self.eval_records = []
        self.eval_begin.configure(state="disabled")
        self.eval_cancel.configure(state="normal")
        self._eval_arm_next()

    def _eval_count(self, z):
        return sum(1 for r in self.eval_records if r[0] == z)

    def _eval_arm_next(self, delay=0.0):
        layout = self.profile.layout
        nxt = next((z for z in range(layout.count) if self._eval_count(z) < EVAL_TAPS_PER_ZONE), None)
        self.armed_zone = nxt
        self.ignore_until = time.monotonic() + delay
        for z in range(layout.count):
            self.eval_map.set_zone(z, "target" if z == nxt else "idle",
                                   f"{self._eval_count(z)} / {EVAL_TAPS_PER_ZONE}")
        if nxt is None:
            self._eval_finish()
        else:
            self.eval_msg.set(f"{layout.instruction(nxt)}. "
                              f"{EVAL_TAPS_PER_ZONE - self._eval_count(nxt)} tap(s) left in this zone.")

    def _eval_observe(self, obs: Observation):
        z = self.armed_zone
        if z is None:
            return
        decision = self.classifier.predict(obs.feature)
        latency = (time.perf_counter() - obs.detected_at) * 1000
        self.eval_records.append((z, decision.zone if decision.accepted else None, latency))
        done = self._eval_count(z) >= EVAL_TAPS_PER_ZONE
        self._eval_arm_next(delay=TRANSITION_SECONDS if done else 0.0)

    def _eval_cancel(self):
        self.mode = "idle"
        self.armed_zone = None
        self.eval_map.reset()
        self.eval_begin.configure(state="normal")
        self.eval_cancel.configure(state="disabled")
        self.eval_msg.set("Test cancelled.")

    def _eval_finish(self):
        self.mode = "idle"
        self.eval_begin.configure(state="normal")
        self.eval_cancel.configure(state="disabled")
        recs = self.eval_records
        correct = sum(1 for e, p, _ in recs if e == p)
        acc = correct / max(len(recs), 1)
        median = statistics.median(r[2] for r in recs) if recs else 0
        layout = self.profile.layout
        zones = range(layout.count)
        lines = [f"Overall accuracy: {acc:.0%} ({correct}/{len(recs)})   target ≥ 80%",
                 f"Median processing latency: {median:.0f} ms   target < 200 ms", "",
                 "Confusion (rows = tapped, columns = recognized):",
                 "        " + "  ".join(f"{layout.short_names[z]:>4}" for z in zones) + "  REJ"]
        for z in zones:
            cells = [sum(1 for e, p, _ in recs if e == z and p == q) for q in zones]
            rej = sum(1 for e, p, _ in recs if e == z and p is None)
            lines.append(f"  {layout.short_names[z]:>4}  " + "  ".join(f"{c:>4}" for c in cells) + f"  {rej:>3}")
            self.eval_map.set_zone(z, "idle", f"{cells[z]} / {EVAL_TAPS_PER_ZONE} correct")
        self.eval_report.configure(state="normal")
        self.eval_report.delete("1.0", "end")
        self.eval_report.insert("1.0", "\n".join(lines))
        self.eval_report.configure(state="disabled")
        self.eval_msg.set("Test complete.")
        try:
            path = data_dir() / f"evaluation-{time.strftime('%Y%m%d-%H%M%S')}.txt"
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    # -------------------------------------------------------------- actions
    def _rebuild_action_zones(self):
        for child in self.action_zone_row.winfo_children():
            child.destroy()
        self.segments = [s for s in self.segments if s.winfo_exists()]
        layout = self.profile.layout
        if self.action_zone.get() >= layout.count:
            self.action_zone.set(0)
        seg = Segmented(self.action_zone_row, self.skin, list(layout.names), value=self.action_zone.get(),
                        command=lambda z: (self.action_zone.set(z), self._load_action_rows()))
        seg.pack(side="left")
        self.segments.append(seg)
        self._load_action_rows()

    def _load_action_rows(self):
        z = self.action_zone.get()
        self._suppress_action_save = True
        try:
            for pattern, (kind_var, text_var, *_rest) in enumerate(self.action_rows):
                action = self.profile.actions[z][pattern]
                kind_var.set(act.ACTION_KINDS[action.kind])
                text_var.set(action.text)
            for pattern in range(len(self.action_rows)):
                self._refresh_action_row(pattern)
        finally:
            self._suppress_action_save = False

    def _action_edited(self, pattern: int):
        if self._suppress_action_save:
            return
        kind_var, text_var, *_ = self.action_rows[pattern]
        keys, labels = list(act.ACTION_KINDS), list(act.ACTION_KINDS.values())
        kind = keys[labels.index(kind_var.get())]
        self.profile.actions[self.action_zone.get()][pattern] = act.ZoneAction(kind, text_var.get())
        for p in range(len(self.action_rows)):
            self._refresh_action_row(p)
        self._persist()

    def _refresh_action_row(self, pattern: int):
        _, _, entry, browse, hint = self.action_rows[pattern]
        z = self.action_zone.get()
        kind = self.profile.actions[z][pattern].kind
        entry.configure(state="normal" if kind in act.TEXT_HINTS else "disabled")
        browse.configure(state="normal" if kind in ("openItem", "sound") else "disabled")
        text = act.TEXT_HINTS.get(kind, "")
        if kind == "none":
            text = "No action."
        hint.configure(text=text)

    def _browse(self, pattern: int):
        kind = self.profile.actions[self.action_zone.get()][pattern].kind
        types = [("Sounds", "*.wav")] if kind == "sound" else [("All files", "*.*")]
        path = filedialog.askopenfilename(filetypes=types)
        if not path and kind == "openItem":
            path = filedialog.askdirectory(title="…or choose a folder")
        if path:
            self.action_rows[pattern][1].set(path.replace("/", "\\"))

    def _test_action(self, pattern: int):
        z = self.action_zone.get()
        action = self.profile.actions[z][pattern]
        if not action.is_configured:
            messagebox.showinfo("Holo", "This action is not fully configured yet.")
            return
        act.perform_async(action, lambda m: self.events.put(("error", m)), self._set_clipboard)
        self._set_status(f"Tested {self.profile.layout.names[z]} {PATTERN_NAMES[pattern].lower()}: "
                         f"{act.ACTION_KINDS[action.kind]}")

    # ---------------------------------------------------------------- misc
    def _retrain(self):
        try:
            self.classifier = (TapClassifier.train(self.profile.positives, self.profile.negatives,
                                                   zone_count=self.profile.zone_count)
                               if self.profile.positives else None)
        except TrainingError as exc:
            self.classifier = None
            self._set_status(f"Profile could not be trained: {exc}")

    def _persist(self):
        try:
            save_profile(self.profile)
        except OSError as exc:
            self._set_status(f"Could not save profile: {exc}")

    def _set_status(self, text: str):
        if hasattr(self, "status"):
            self.status.set(text)

    # ------------------------------------------------------------ background
    def _close_window(self):
        if not self.tray.available:
            self._quit()
            return
        if self.mode in ("calibrate", "evaluate", "negatives"):
            if not messagebox.askyesno("Holo", "Stop the current calibration or test and keep Holo "
                                       "running in the background?"):
                return
            if self.mode == "evaluate":
                self._eval_cancel()
            elif self.mode == "calibrate":
                self._cal_cancel()
            else:
                self._neg_finish()
        self._hide_window(announce=not self.settings.get("tray_hint_shown"))

    def _hide_window(self, announce: bool):
        self.hidden = True
        self.root.withdraw()
        self._tab_changed()
        if announce:
            self.tray.notify("Holo keeps listening in the background. Right-click the icon next "
                             "to the clock to open or exit it.")
            self._save_setting("tray_hint_shown", True)

    def _tray_heartbeat(self):
        self.tray.reassert()
        self._tray_id = self.root.after(TRAY_REFRESH_MS, self._tray_heartbeat)

    def _show_window(self):
        self.tray.reassert()
        self.hidden = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._tab_changed()

    def _toggle_actions(self):
        self.actions_enabled.set(not self.actions_enabled.get())
        self.tray.refresh()

    def _toggle_autostart(self):
        self.autostart_var.set(not self.autostart_var.get())
        self._autostart_clicked()

    def _autostart_clicked(self):
        try:
            bg.set_autostart(self.autostart_var.get())
        except OSError as exc:
            self.autostart_var.set(bg.autostart_enabled())
            self._set_status(f"Could not change start-with-Windows: {exc}")
        if hasattr(self, "tray"):
            self.tray.refresh()

    def _load_settings(self) -> dict:
        try:
            return json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_setting(self, key, value):
        self.settings[key] = value
        try:
            (data_dir() / "settings.json").write_text(json.dumps(self.settings), encoding="utf-8")
        except OSError:
            pass

    def _quit(self):
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        if hasattr(self, "tray"):
            self.tray.stop()
        for timer in (getattr(self, "_tick_id", None), getattr(self, "_pump_id", None),
                      getattr(self, "_tray_id", None)):
            if timer:
                self.root.after_cancel(timer)
        self.audio.stop()
        self.root.destroy()


def main():
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        from .selftest import run
        sys.exit(run(sys.argv[2] if len(sys.argv) > 2 else "selftest.txt"))
    try:  # crisp text on high-DPI displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass
    instance = bg.SingleInstance()
    if not instance.is_primary:
        instance.signal_primary()  # bring the running copy to the front instead
        return
    bg.set_app_user_model_id()
    root = tk.Tk()
    HoloApp(root, start_hidden="--background" in sys.argv, instance=instance)
    root.mainloop()


if __name__ == "__main__":
    main()
