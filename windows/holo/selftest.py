# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""`Holo.exe --self-test <report>`: launches the UI headlessly and runs the DSP pipeline
on synthetic taps, so a packaged build can be verified without a microphone."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

import numpy as np


def _synthetic_taps(rng, per_zone, zones=(0, 1, 2, 3)):
    from .core import LabeledTap, StreamingTapDetector, TapFeatureExtractor
    sr = 48000.0
    det, ex = StreamingTapDetector(sr, 1), TapFeatureExtractor(sr)
    quiet = lambda k: rng.normal(0, 0.0004, k).astype(np.float32)

    def feed(sig):
        out = []
        for i in range(0, len(sig), 512):
            out += det.process(sig[i:i + 512])
        return out

    feed(quiet(int(sr)))
    taps = []
    for z in zones:
        got = 0
        while got < per_zone:
            n = int(sr * 0.12)
            t = np.arange(n) / sr
            f1, f2 = 600 + 500 * int(z), 2400 + 900 * (3 - int(z))
            s = 0.25 * (np.exp(-t * 90) * np.sin(2 * np.pi * f1 * t)
                        + 0.5 * np.exp(-t * 140) * np.sin(2 * np.pi * f2 * t))
            s[:96] += 0.37 * rng.normal(0, 1, 96) * np.exp(-np.arange(96) / 12)
            events = feed(np.concatenate([quiet(int(rng.integers(40, 450))), s.astype(np.float32),
                                          quiet(int(sr * 0.25))]))
            if events:
                taps.append(LabeledTap(z, ex.extract(events[0])))
                got += 1
    return taps


def run(report_path: str) -> int:
    lines = []
    ok = True
    try:
        os.environ["APPDATA"] = tempfile.mkdtemp()  # never touch the user's real profile
        import tkinter as tk
        from .app import HoloApp
        from .core import TapClassifier, leave_one_out
        from . import audio
        from . import background as bg

        try:
            lines.append(f"input devices: {audio.list_input_devices()}")
        except Exception as exc:
            lines.append(f"input devices: unavailable ({exc})")

        root = tk.Tk()
        app = HoloApp(root)
        for _ in range(20):
            root.update()
        locked = str(app.tabs.tab(0, "state")) == "disabled"
        lines.append(f"first run requires calibration: {locked}")
        ok &= locked

        rng = np.random.default_rng(1)
        train, test = _synthetic_taps(rng, 10), _synthetic_taps(rng, 5)
        model = TapClassifier.train(train)
        acc = sum(model.predict(t.feature).zone == t.zone for t in test) / len(test)
        loo, _ = leave_one_out(train, 4)
        lines.append(f"synthetic accuracy: {acc:.2f}, leave-one-out: {loo:.2f}")
        ok &= acc >= 0.9
        single = TapClassifier.train(_synthetic_taps(rng, 10, (0,)), zone_count=1)
        hits = sum(single.predict(t.feature).accepted for t in _synthetic_taps(rng, 5, (0,)))
        lines.append(f"single-zone taps recognized: {hits}/5")
        ok &= hits >= 4
        icon_ok = bg.ICON_ICO.exists() and bg.ICON_PNG.exists()
        lines.append(f"icon files bundled: {icon_ok}")
        ok &= icon_ok
        app._quit()
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("SELF-TEST PASSED" if ok else "SELF-TEST FAILED")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "selftest.txt"))
