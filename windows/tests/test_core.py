# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
import numpy as np
import pytest

from holo.core import (LabeledTap, StreamingTapDetector, TapClassifier, TapFeatureExtractor,
                       TapPatternTracker, leave_one_out)

SR = 48000.0


def feed(detector, signal, block=512):
    events = []
    for i in range(0, len(signal), block):
        events += detector.process(signal[i:i + block])
    return events


def test_ignores_steady_background_and_finds_impulse():
    d = StreamingTapDetector(SR, 1)
    assert feed(d, np.full(512 * 80, 0.0004, dtype=np.float32)) == []
    impulse = np.full(512, 0.0004, dtype=np.float32)
    impulse[120], impulse[121] = 0.2, -0.13
    events = d.process(impulse) + feed(d, np.full(512 * 10, 0.0002, dtype=np.float32))
    assert len(events) == 1
    assert events[0].channels.shape[1] == d.window_samples
    assert events[0].onset_offset > 0


def test_rejects_sustained_tone():
    d = StreamingTapDetector(SR, 1, warm_up_duration=0)
    t = np.arange(int(SR * 0.6)) / SR
    tone = (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    sig = np.concatenate([np.zeros(4096, np.float32), tone])
    assert feed(d, sig) == []


def synth_tap(zone: int, rng):
    """Damped resonances whose frequencies depend on the zone."""
    n = int(SR * 0.12)
    t = np.arange(n) / SR
    f1 = 600 + 500 * int(zone)
    f2 = 2400 + 900 * (3 - int(zone))
    amp = 0.25 * rng.uniform(0.8, 1.2)
    sig = amp * (np.exp(-t * 90) * np.sin(2 * np.pi * f1 * rng.uniform(0.97, 1.03) * t)
                 + 0.5 * np.exp(-t * 140) * np.sin(2 * np.pi * f2 * rng.uniform(0.97, 1.03) * t))
    sig[:96] += amp * 1.5 * rng.normal(0, 1, 96) * np.exp(-np.arange(96) / 12)  # contact click
    sig += rng.normal(0, 0.0004, n)
    return sig.astype(np.float32)


def capture_taps(rng, per_zone=10, zones=(0, 1, 2, 3)):
    d = StreamingTapDetector(SR, 1)
    ex = TapFeatureExtractor(SR)
    quiet = lambda k: rng.normal(0, 0.0004, k).astype(np.float32)
    samples = []
    feed(d, quiet(int(SR)))
    misses = 0
    for z in zones:
        got = 0
        while got < per_zone:
            lead = quiet(int(rng.integers(40, 450)))  # real taps land mid-block
            events = feed(d, np.concatenate([lead, synth_tap(z, rng), quiet(int(SR * 0.25))]))
            assert len(events) <= 1
            if events:
                samples.append(LabeledTap(z, ex.extract(events[0])))
                got += 1
            else:
                misses += 1
    # The original detector's onset rules miss some of these synthetic taps (a slowly ringing
    # low tone looks sustained to it); the port keeps that behaviour unchanged.
    assert misses <= 3 + len(samples) * 0.4
    return samples


def test_pipeline_classifies_synthetic_zones():
    rng = np.random.default_rng(1)
    train = capture_taps(rng)
    test = capture_taps(rng, per_zone=5)
    model = TapClassifier.train(train)
    correct = sum(model.predict(s.feature).zone == s.zone for s in test)
    assert correct / len(test) >= 0.9
    acc, per_zone = leave_one_out(train, 4)
    assert acc >= 0.9 and len(per_zone) == 4


def test_rejects_out_of_distribution_and_weak():
    rng = np.random.default_rng(2)
    model = TapClassifier.train(capture_taps(rng))
    ex = TapFeatureExtractor(SR)
    # A noise burst is unlike any calibrated tap.
    d = StreamingTapDetector(SR, 1)
    burst = np.concatenate([rng.normal(0, 0.0004, int(SR)),
                            rng.normal(0, 0.3, 300) * np.exp(-np.arange(300) / 60),
                            rng.normal(0, 0.0004, int(SR * 0.3))]).astype(np.float32)
    events = feed(d, burst)
    assert events
    assert not model.predict(ex.extract(events[0])).accepted


def test_profile_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from holo import storage
    from holo.actions import ZoneAction
    rng = np.random.default_rng(3)
    p = storage.Profile(positives=capture_taps(rng, per_zone=3))
    p.actions[2][1] = ZoneAction("openURL", "claude.ai")
    storage.save_profile(p)
    loaded = storage.load_profile()
    assert len(loaded.positives) == 12
    assert loaded.actions[2][1].kind == "openURL"
    assert loaded.positives[0].feature.values == pytest.approx(p.positives[0].feature.values)


def test_key_parsing():
    from holo.actions import parse_keys, ActionError
    assert parse_keys("ctrl+shift+esc") == [0x11, 0x10, 0x1B]
    assert parse_keys("win+d") == [0x5B, ord("D")]
    with pytest.raises(ActionError):
        parse_keys("ctrl+banana")


def test_two_zone_layout():
    rng = np.random.default_rng(4)
    train = capture_taps(rng, zones=(0, 1))
    test = capture_taps(rng, per_zone=5, zones=(0, 1))
    model = TapClassifier.train(train, zone_count=2)
    assert sum(model.predict(s.feature).zone == s.zone for s in test) / len(test) >= 0.9


def test_single_zone_accepts_taps_and_rejects_unlike_sounds():
    rng = np.random.default_rng(5)
    model = TapClassifier.train(capture_taps(rng, zones=(0,)), zone_count=1)
    accepted = [model.predict(s.feature) for s in capture_taps(rng, per_zone=8, zones=(0,))]
    assert sum(d.accepted and d.zone == 0 for d in accepted) >= 7
    # Taps with a very different sound (another "zone") are not like the calibrated taps.
    other = [model.predict(s.feature) for s in capture_taps(rng, per_zone=5, zones=(3,))]
    assert sum(d.accepted for d in other) <= 1
    acc, per_zone = leave_one_out(capture_taps(rng, zones=(0,)), 1)
    assert acc >= 0.8 and len(per_zone) == 1


def test_tap_patterns():
    configured = {0: 3, 1: 1}            # zone 0 has up to triple-tap actions, zone 1 only single
    t = TapPatternTracker(lambda z, n: n < configured[z], gap=0.6)
    assert t.add(1, 0.0) == [(1, 1)]     # nothing longer configured -> fires at once
    assert t.add(0, 1.0) == []           # waits: a double or triple may follow
    assert t.add(0, 1.3) == []
    assert t.add(0, 1.6) == [(0, 3)]     # triple fires immediately
    assert t.add(0, 3.0) == []
    assert t.poll(3.3) == []
    assert t.poll(3.7) == [(0, 1)]       # gap passed -> single
    assert t.add(0, 5.0) == []
    assert t.add(0, 5.3) == []
    assert t.add(1, 5.5) == [(0, 2), (1, 1)]   # other zone ends the double, then its own single
    assert t.add(0, 7.0) == []
    assert t.add(0, 7.9) == [(0, 1)]     # too slow: the first tap already counts as a single
    assert t.poll(8.6) == [(0, 1)]       # ...and so does the second


def test_v1_profile_migrates(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from holo import storage
    rng = np.random.default_rng(6)
    p = storage.Profile(positives=capture_taps(rng, per_zone=2))
    d = p.to_dict()
    d["version"] = 1
    d.pop("zone_count")
    d["actions"] = [{"kind": "openURL", "text": f"site{i}.com"} for i in range(4)]
    (tmp_path / "Holo").mkdir(exist_ok=True)
    (tmp_path / "Holo" / "profile.json").write_text(json.dumps(d))
    loaded = storage.load_profile()
    assert loaded.zone_count == 4
    assert loaded.actions[2][0].text == "site2.com"
    assert [a.kind for a in loaded.actions[2][1:]] == ["none", "none"]


def test_sensitivity_strict_accepts_no_more_than_sensitive():
    import dataclasses
    rng = np.random.default_rng(8)
    train = capture_taps(rng, zones=(0, 1))
    model = TapClassifier.train(train, zone_count=2)
    other = capture_taps(rng, per_zone=4, zones=(3,))
    # Sounds part-way between a calibrated tap and an unknown one: the borderline cases
    # where the sensitivity setting decides.
    probes = []
    for a, b in zip(train[:4], other):
        for w in np.linspace(0.0, 1.0, 11):
            values = [(1 - w) * x + w * y for x, y in zip(a.feature.values, b.feature.values)]
            probes.append(dataclasses.replace(a.feature, values=values))
    accepted = {s: sum(model.predict(p, s).accepted for p in probes) for s in (1, 3, 5)}
    assert accepted[1] <= accepted[3] <= accepted[5]
    assert accepted[1] < accepted[5]


def test_strict_detector_ignores_quieter_taps():
    rng = np.random.default_rng(0)
    quiet = lambda k: rng.normal(0, 0.0004, k).astype(np.float32)
    soft_tap = synth_tap(2, np.random.default_rng(1)) * 0.035
    results = {}
    for loudness in (1.0, 2.2):
        d = StreamingTapDetector(SR, 1)
        d.loudness = loudness
        feed(d, quiet(int(SR)))
        results[loudness] = feed(d, np.concatenate([quiet(200), soft_tap, quiet(12000)]))
    assert len(results[1.0]) == 1 and results[2.2] == []


def test_three_zone_layout_is_left_right_front():
    from holo.core import LAYOUTS
    layout = LAYOUTS[3]
    assert layout.names == ("Left", "Right", "Front") and layout.has_front
    assert {LAYOUTS[n].taps for n in LAYOUTS} == {20, 12, 10}
    assert all(layout.taps_per_zone(z) == 12 for z in range(3))


def test_old_three_zone_profile_needs_recalibration(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from holo import storage
    from holo.actions import ZoneAction
    p = storage.Profile(positives=capture_taps(np.random.default_rng(9), per_zone=2, zones=(0, 1, 2)),
                        zone_count=3, actions=storage.empty_actions(3))
    p.actions[1][0] = ZoneAction("openURL", "keep.me")
    d = p.to_dict()
    d["version"] = 2
    (tmp_path / "Holo").mkdir(exist_ok=True)
    (tmp_path / "Holo" / "profile.json").write_text(json.dumps(d))
    loaded = storage.load_profile()
    assert loaded.positives == [] and loaded.actions[1][0].text == "keep.me"


def _reference_lowpass(values, alpha, state):
    """The original recursive filter, sample by sample (as in the Swift detector)."""
    out = np.asarray(values, dtype=np.float64)
    for stage in range(4):
        res = np.empty_like(out)
        s = state[stage]
        for i, x in enumerate(out):
            s += alpha * (x - s)
            res[i] = s
        state[stage] = s
        out = res
    return out


@pytest.mark.parametrize("sample_rate", [16000.0, 44100.0, 48000.0, 96000.0])
def test_streaming_lowpass_matches_recursive_filter(sample_rate):
    from holo.core import OnsetLowPass, _onset_alpha, _lowpass4
    alpha = _onset_alpha(sample_rate)
    rng = np.random.default_rng(1)
    signal = rng.normal(0, 0.1, 5000)
    signal[1000:1010] += 0.8
    expected = _reference_lowpass(signal, alpha, np.zeros(4))
    stream = OnsetLowPass(alpha)
    got = np.concatenate([stream(signal[i:i + 512]) for i in range(0, signal.size, 512)])
    assert np.max(np.abs(got - expected)) < 1e-9
    assert np.max(np.abs(_lowpass4(signal, alpha) - expected)) < 1e-9


@pytest.mark.parametrize("interval", [0.13, 0.17, 0.22])
def test_desk_detector_hears_quick_double_taps(interval):
    def taps_heard(refractory):
        heard = 0
        rng = np.random.default_rng(int(interval * 1000))
        quiet = lambda k: rng.normal(0, 0.0004, k).astype(np.float32)
        for _ in range(10):
            d = StreamingTapDetector(SR, 1)
            d.refractory_after_tap = refractory
            feed(d, quiet(int(SR)))
            a, b = synth_tap(1, rng), synth_tap(1, rng)
            gap = int(SR * interval)
            if gap < a.size:   # second tap starts while the first still rings
                middle = [a[:gap], a[gap:] + b[:a.size - gap], b[a.size - gap:]]
            else:
                middle = [a, quiet(gap - a.size), b]
            heard += len(feed(d, np.concatenate([quiet(200), *middle, quiet(int(SR * 0.3))]))) >= 2
        return heard
    assert taps_heard(0.14) == 0          # the original pause swallows the second tap
    assert taps_heard(0.03) >= 8          # the Desk setting hears it


def test_short_pause_does_not_split_one_ringing_tap():
    rng = np.random.default_rng(3)
    n = int(SR * 0.4)
    t = np.arange(n) / SR
    for _ in range(10):
        d = StreamingTapDetector(SR, 1)
        d.refractory_after_tap, d.pattern_followup = 0.03, True
        quiet = lambda k: rng.normal(0, 0.0004, k).astype(np.float32)
        feed(d, quiet(int(SR)))
        ringing = (0.25 * (np.exp(-t * 20) * np.sin(2 * np.pi * 1100 * t)
                           + 0.5 * np.exp(-t * 30) * np.sin(2 * np.pi * 3300 * t))).astype(np.float32)
        ringing[:96] += (0.37 * rng.normal(0, 1, 96) * np.exp(-np.arange(96) / 12)).astype(np.float32)
        assert len(feed(d, np.concatenate([quiet(300), ringing, quiet(int(SR * 0.5))]))) <= 1


def test_shortcut_target_skips_holo_and_hidden_windows(monkeypatch):
    import ctypes
    from holo import actions
    monkeypatch.setattr(actions, "_is_cloaked", lambda hwnd: False)
    own = 100
    # z-order below Holo's window (1): its own popup, a hidden window, a tool window,
    # an untitled window, then the app the user was really using.
    windows = {2: (own, True, 0, 5), 3: (200, False, 0, 5), 4: (200, True, 0x80, 5),
               5: (200, True, 0, 0), 6: (300, True, 0, 9)}
    order = [1, 2, 3, 4, 5, 6]

    class User32:
        def GetWindow(self, hwnd, cmd):
            i = order.index(hwnd) + 1
            return order[i] if i < len(order) else 0

        def GetWindowThreadProcessId(self, hwnd, pid_ref):
            ctypes.cast(pid_ref, ctypes.POINTER(ctypes.c_ulong)).contents.value = windows[hwnd][0]

        def IsWindowVisible(self, hwnd):
            return windows[hwnd][1]

        def IsIconic(self, hwnd):
            return False

        def GetWindowLongW(self, hwnd, index):
            return windows[hwnd][2]

        def GetWindowTextLengthW(self, hwnd):
            return windows[hwnd][3]

    assert actions._app_behind(User32(), own, 1) == 6


def test_modifier_order_does_not_depend_on_spelling():
    from holo.actions import parse_keys
    assert parse_keys("alt+shift") == parse_keys("shift+alt") == [0x12, 0x10]
    assert parse_keys("shift+win+ctrl+s") == [0x11, 0x10, 0x5B, ord("S")]


def test_holo_comes_back_only_if_the_shortcut_left_focus_alone():
    from holo import actions

    class User32:
        def __init__(self, foreground):
            self.foreground, self.raised = foreground, []

        def GetForegroundWindow(self):
            return self.foreground

        def SetWindowPos(self, hwnd, after, *rest):
            self.raised.append((hwnd, after))

    stayed = User32(foreground=7)            # keys went to app 7, which still has the focus
    actions._return_focus_to_holo(stayed, holo=1, target=7)
    assert stayed.raised == [(1, -1), (1, -2)]
    moved = User32(foreground=9)             # e.g. alt+tab switched to another app
    actions._return_focus_to_holo(moved, holo=1, target=7)
    assert moved.raised == []
