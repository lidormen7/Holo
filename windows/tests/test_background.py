# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Windows tray-icon behaviour, exercised with a stand-in for pystray's Windows backend."""

import ctypes
import sys
import types

import pytest

from holo import background as bg


@pytest.fixture
def fake_pystray(monkeypatch):
    calls = []

    class NOTIFYICONDATAW(ctypes.Structure):
        # Same field names as pystray._util.win32.NOTIFYICONDATAW.
        _fields_ = [("cbSize", ctypes.c_uint), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint),
                    ("uFlags", ctypes.c_uint)]

    win32 = types.SimpleNamespace(
        NOTIFYICONDATAW=NOTIFYICONDATAW,
        Shell_NotifyIcon=lambda code, data: calls.append((code, data.uID)),
    )

    class Icon:
        def __init__(self, name, image, title, menu=None):
            self._hwnd = 1234

        def run_detached(self):
            self._message("ADD", 0)

        def _hide(self):
            self._message("DELETE", 0)

        def stop(self):
            calls.append(("STOP", None))

        def update_menu(self):
            pass

        def _message(self, code, flags, **kwargs):  # pystray 0.19's own: "hID" never reaches uID
            data = win32.NOTIFYICONDATAW(cbSize=0, hWnd=self._hwnd, uFlags=flags)
            data.hID = id(self)
            win32.Shell_NotifyIcon(code, data)

    class Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            pass

    pystray = types.ModuleType("pystray")
    pystray.Icon, pystray.Menu, pystray.MenuItem = Icon, Menu, lambda *a, **k: None
    util = types.ModuleType("pystray._util")
    util.win32 = win32
    monkeypatch.setitem(sys.modules, "pystray", pystray)
    monkeypatch.setitem(sys.modules, "pystray._util", util)
    monkeypatch.setattr(bg, "IS_WINDOWS", True)
    monkeypatch.setattr(bg.atexit, "register", lambda fn: None)
    return calls


def make_tray():
    noop = lambda *a: None
    return bg.TrayIcon(on_open=noop, on_toggle_listen=noop, is_listening=lambda: True,
                       on_toggle_actions=noop, actions_enabled=lambda: True,
                       on_toggle_autostart=noop, on_exit=noop)


def test_tray_icon_uses_fixed_id_and_is_removed_before_stopping(fake_pystray):
    tray = make_tray()
    assert tray.available
    tray.stop()
    tray.stop()  # second call (e.g. at exit) does nothing
    assert fake_pystray == [("ADD", bg.TRAY_ICON_ID), ("DELETE", bg.TRAY_ICON_ID), ("STOP", None)]


def test_same_id_on_every_run(fake_pystray):
    for _ in range(2):
        make_tray().stop()
    ids = {hid for code, hid in fake_pystray if code in ("ADD", "DELETE")}
    assert ids == {bg.TRAY_ICON_ID}
