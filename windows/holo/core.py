# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Signal processing core for Holo on Windows.

A NumPy port of the original HoloCore Swift module (StreamingTapDetector,
ImpactEventGate, TapFeatureExtractor, TrainedTapClassifier). The constants and
decision rules are kept identical so behaviour matches the macOS app. Only the
passive sensing strategy is ported; the experimental ultrasonic probe is not.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from functools import lru_cache

import numpy as np


# --------------------------------------------------------------------------- zones

# Desk regions around the laptop. "LR" = left, rear (screen side); "LF" = left, front
# (keyboard side); "L" = the whole left side; "F" = in front of the laptop, between the
# user and the keyboard. A layout groups regions into 1-4 zones labelled 0..count-1.
REGION_TEXT = {
    "LR": "on the left of the laptop, near the screen edge",
    "LF": "on the left of the laptop, near the keyboard edge",
    "RR": "on the right of the laptop, near the screen edge",
    "RF": "on the right of the laptop, near the keyboard edge",
    "L": "on the left of the laptop, spreading taps front and back",
    "R": "on the right of the laptop, spreading taps front and back",
    "F": "in front of the laptop, between you and the keyboard, spreading taps left to right",
}


@dataclass(frozen=True)
class ZoneLayout:
    count: int
    names: tuple
    short_names: tuple
    regions: tuple            # per zone: tuple of region ids (see REGION_TEXT)
    taps: int                 # calibration taps per zone; bigger zones need more

    def taps_per_zone(self, zone: int) -> int:
        return self.taps

    def instruction(self, zone: int) -> str:
        regions = self.regions[zone]
        if len(regions) > 1:
            return "Tap anywhere around the laptop: left, right and in front, spreading taps out"
        return "Tap the desk " + REGION_TEXT[regions[0]]

    @property
    def has_front(self) -> bool:
        return any("F" in r for r in self.regions)


LAYOUTS = {
    1: ZoneLayout(1, ("Anywhere",), ("ANY",), (("L", "R", "F"),), 20),
    2: ZoneLayout(2, ("Left", "Right"), ("L", "R"), (("L",), ("R",)), 12),
    3: ZoneLayout(3, ("Left", "Right", "Front"), ("L", "R", "F"), (("L",), ("R",), ("F",)), 12),
    4: ZoneLayout(4, ("Left Rear", "Left Front", "Right Rear", "Right Front"), ("LR", "LF", "RR", "RF"),
                  (("LR",), ("LF",), ("RR",), ("RF",)), 10),
}

# Tap sensitivity 1 (strict) .. 5 (sensitive). Strict needs louder taps to be heard and
# a closer match to the calibration before an action runs.
SENSITIVITY_NAMES = {1: "Strict", 2: "Careful", 3: "Balanced", 4: "Responsive", 5: "Sensitive"}
SENSITIVITY_LOUDNESS = {1: 2.2, 2: 1.5, 3: 1.0, 4: 0.8, 5: 0.65}     # detector threshold multiplier
SENSITIVITY_MATCH = {1: 0.80, 2: 0.90, 3: 1.0, 4: 1.1, 5: 1.2}       # novelty-threshold multiplier

REJECTION_TEXT = {
    "weakSignal": "Signal too weak",
    "lowSignalToNoise": "Background noise too high",
    "clippedSignal": "Signal clipped",
    "outOfDistribution": "Unlike calibrated taps",
    "ambiguousZone": "Zone ambiguous",
    "resemblesNegativeExample": "Recognized non-desk sound",
    "schemaMismatch": "Profile is incompatible",
}


# ------------------------------------------------------------------ signal models

MIN_RELIABLE_PEAK = 0.003
MAX_RELIABLE_CLIPPING = 0.20
MIN_CLASSIFICATION_SNR_DB = 6.0
GUIDED_MIN_SNR_DB = 7.0


@dataclass
class SignalQuality:
    snr_db: float
    peak: float
    rms: float
    clipping: float
    noise_floor: float
    duration_ms: float

    @property
    def score(self) -> float:
        snr = min(max((self.snr_db - 4) / 30, 0), 1)
        strength = min(max((self.peak - MIN_RELIABLE_PEAK) / 0.15, 0), 1)
        clean = 1 - min(self.clipping * 4, 1)
        return 0.55 * snr + 0.25 * strength + 0.20 * clean

    @property
    def summary(self) -> str:
        if self.clipping > MAX_RELIABLE_CLIPPING:
            return "Clipped"
        if self.peak < MIN_RELIABLE_PEAK:
            return "Weak"
        if self.snr_db < MIN_CLASSIFICATION_SNR_DB:
            return "Noisy"
        if self.score > 0.72:
            return "Excellent"
        if self.score > 0.48:
            return "Good"
        return "Fair"

    def guided_issue(self) -> Optional[str]:
        """Retry guidance for a calibration sample, or None if it is usable."""
        if self.clipping > MAX_RELIABLE_CLIPPING:
            return "That tap clipped. Use a lighter, more natural tap."
        if self.peak < MIN_RELIABLE_PEAK:
            return "That tap was too soft. Try a slightly firmer tap."
        if self.snr_db < GUIDED_MIN_SNR_DB:
            return "Background noise masked that tap. Wait for quiet, then try again."
        return None


@dataclass
class TapFeature:
    names: list[str]
    values: list[float]
    quality: SignalQuality
    strategy: str = "passive"
    captured_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TapFeature":
        return TapFeature(
            names=list(d["names"]),
            values=[float(v) for v in d["values"]],
            quality=SignalQuality(**d["quality"]),
            strategy=d.get("strategy", "passive"),
            captured_at=float(d.get("captured_at", 0)),
        )


@dataclass
class LabeledTap:
    zone: Optional[int]
    feature: TapFeature
    negative_label: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "zone": None if self.zone is None else int(self.zone),
            "negative_label": self.negative_label,
            "feature": self.feature.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "LabeledTap":
        zone = d.get("zone")
        return LabeledTap(
            zone=None if zone is None else int(zone),
            feature=TapFeature.from_dict(d["feature"]),
            negative_label=d.get("negative_label"),
        )


@dataclass
class DetectedTap:
    channels: np.ndarray        # shape (channels, samples), float32
    onset_offset: int
    stream_sample_index: int
    noise_floor_rms: float


# ------------------------------------------------------------------- filtering

def _onset_alpha(sample_rate: float) -> float:
    cutoff = min(6000.0, sample_rate * 0.20)
    return 1 - math.exp(-2 * math.pi * cutoff / sample_rate)


@lru_cache(maxsize=8)
def _lowpass_kernel(alpha: float) -> np.ndarray:
    """Impulse response of four cascaded one-pole low-pass stages (s += alpha * (x - s)).

    The response decays geometrically, so truncating it once the tail is below 1e-12 of
    the peak gives the same output as the recursive filter to within rounding, while a
    short convolution runs entirely in NumPy.
    """
    n = 32
    while True:
        stage = alpha * (1 - alpha) ** np.arange(n)
        kernel = stage
        for _ in range(3):
            kernel = np.convolve(kernel, stage)[:n]
        if kernel[-8:].max() < 1e-12 * kernel.max() or n >= 1 << 14:
            return kernel
        n *= 2


class OnsetLowPass:
    """Streaming version of the four-stage low-pass used for onset detection."""

    def __init__(self, alpha: float):
        self.kernel = _lowpass_kernel(alpha)
        self.reset()

    def reset(self) -> None:
        self._history = np.zeros(self.kernel.size - 1)

    def __call__(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        extended = np.concatenate([self._history, x])
        start = self._history.size
        self._history = extended[-start:] if start else extended[:0]
        return np.convolve(extended, self.kernel)[start:start + x.size]


def _lowpass4(values: np.ndarray, alpha: float) -> np.ndarray:
    """Four-stage low-pass of a whole signal starting from silence."""
    x = np.asarray(values, dtype=np.float64)
    return np.convolve(x, _lowpass_kernel(alpha))[:x.size]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


# ------------------------------------------------------------- impact gate

def impact_gate_accepts(event: DetectedTap, sample_rate: float, min_onset_contrast: float = 1.8) -> bool:
    m = impact_metrics(event, sample_rate)
    if m is None:
        return False
    onset_contrast, effective_duration, early_fraction, late_to_impact = m
    if onset_contrast < min_onset_contrast:
        return False
    clearly_sustained = (
        effective_duration >= 0.040 and late_to_impact >= 0.36 and early_fraction < 0.60
    )
    return not clearly_sustained


def impact_metrics(event: DetectedTap, sample_rate: float):
    ch = event.channels
    if sample_rate <= 0 or ch.size == 0 or ch.shape[1] == 0:
        return None
    mono = ch.astype(np.float64).mean(axis=0)
    filtered = _lowpass4(mono, _onset_alpha(sample_rate))
    n = filtered.size
    onset = min(max(event.onset_offset, 0), n - 1)
    frame = max(int(sample_rate * 0.005), 32)

    frame_rms, frame_len = [], []
    off = onset
    while off < n:
        end = min(off + frame, n)
        frame_rms.append(_rms(filtered[off:end]))
        frame_len.append(end - off)
        off = end
    if not frame_rms:
        return None

    search = min(len(frame_rms), max(int(math.ceil(0.025 * sample_rate / frame)), 1))
    impact_rms = max(frame_rms[:search])
    if impact_rms <= 0:
        return None

    pre_start = max(0, onset - int(sample_rate * 0.012))
    pre_rms = _rms(filtered[pre_start:onset])
    ref_floor = max(pre_rms, event.noise_floor_rms, 1e-6)
    search_end = min(onset + search * frame, n)
    impact_peak = float(np.max(np.abs(filtered[onset:search_end]))) if search_end > onset else 0.0
    onset_contrast = max(impact_rms / ref_floor, 0.45 * impact_peak / ref_floor)

    eff_threshold = max(impact_rms * 0.40, event.noise_floor_rms * 2.2)
    eff_samples = sum(l for r, l in zip(frame_rms, frame_len) if r >= eff_threshold)
    eff_duration = eff_samples / sample_rate

    early_end = min(onset + int(sample_rate * 0.025), n)
    total_energy = float(np.sum(filtered[onset:] ** 2))
    early_fraction = float(np.sum(filtered[onset:early_end] ** 2)) / max(total_energy, 1e-15)

    late_start = min(onset + int(sample_rate * 0.040), n)
    late_rms = _rms(filtered[late_start:])
    return onset_contrast, eff_duration, early_fraction, late_rms / max(impact_rms, 1e-12)


# --------------------------------------------------------------- detector

class StreamingTapDetector:
    """Adaptive streaming onset detector emitting fixed 90 ms analysis windows."""

    def __init__(self, sample_rate: float, channel_count: int, analysis_duration: float = 0.090,
                 pre_roll_duration: float = 0.012, warm_up_duration: float = 0.75,
                 initial_noise_floor: float = 0.0005):
        self.sample_rate = float(sample_rate)
        self.channel_count = max(int(channel_count), 1)
        self.window_samples = max(int(sample_rate * analysis_duration), 1024)
        self.pre_roll_samples = max(int(sample_rate * pre_roll_duration), 128)
        self.warm_up_samples = max(int(sample_rate * warm_up_duration), 0)
        self.initial_noise_floor = max(initial_noise_floor, 1e-5)
        self._onset_filter = OnsetLowPass(_onset_alpha(self.sample_rate))
        self.loudness = 1.0   # >1 needs louder taps (sensitivity setting); safe to change live
        # Quiet time after an accepted tap before the next can be heard. The original
        # 140 ms made quick double/triple taps impossible; the Desk uses a shorter one.
        self.refractory_after_tap = 0.14
        # True while a tap pattern is in progress: the next tap may start over the first
        # one's ringing, so the onset-contrast check is relaxed (see impact_gate_accepts).
        self.pattern_followup = False
        self.reset()

    def reset(self) -> None:
        self.total_samples = 0
        self.noise_floor_rms = self.initial_noise_floor
        self._pre_roll = np.zeros((self.channel_count, 0), dtype=np.float32)
        self._capture: Optional[np.ndarray] = None
        self._capture_onset = 0
        self._capture_index = 0
        self._capture_floor = 0.0
        self._refractory = 0
        self._adapt_in_refractory = False
        self._warm_up_remaining = self.warm_up_samples
        self._onset_filter.reset()

    def process(self, block: np.ndarray) -> list[DetectedTap]:
        """block: shape (frames, channels) or (frames,), float32 in [-1, 1]."""
        block = np.asarray(block, dtype=np.float32)
        if block.ndim == 1:
            block = block[:, None]
        frames = block.shape[0]
        if frames == 0:
            return []
        channels = np.empty((self.channel_count, frames), dtype=np.float32)
        for c in range(self.channel_count):
            channels[c] = block[:, min(c, block.shape[1] - 1)]
        mono = channels.mean(axis=0)
        onset_signal = self._onset_filter(mono)
        try:
            return self._process(channels, onset_signal, frames)
        finally:
            self.total_samples += frames

    def _process(self, channels, onset_signal, frames) -> list[DetectedTap]:
        rms = _rms(onset_signal)
        abs_sig = np.abs(onset_signal)
        peak = float(abs_sig.max())

        if self._warm_up_remaining > 0:
            self._adapt(rms, warm_up=True)
            self._warm_up_remaining = max(0, self._warm_up_remaining - frames)
            self._append_pre_roll(channels)
            return []

        if self._refractory > 0:
            if self._adapt_in_refractory:
                self._adapt(rms, warm_up=False)
            self._refractory = max(0, self._refractory - frames)
            if self._refractory == 0:
                self._adapt_in_refractory = False
            self._append_pre_roll(channels)
            return []

        if self._capture is not None:
            self._capture = np.concatenate([self._capture, channels], axis=1)
            ev = self._complete_if_ready()
            return [ev] if ev else []

        rms_threshold = max(self.noise_floor_rms * 1.18, 0.0008)
        peak_threshold = max(self.noise_floor_rms * 4.0, 0.007) * self.loudness
        crest = peak / max(rms, 1e-6)
        strong_threshold = max(peak_threshold, peak * 0.55)
        strong_fraction = float(np.count_nonzero(abs_sig >= strong_threshold)) / max(frames, 1)
        is_impulse = (rms > rms_threshold and peak > peak_threshold
                      and crest > 2.0 and strong_fraction < 0.20)

        if is_impulse:
            crossing = int(np.argmax(abs_sig >= peak_threshold))
            self._capture = self._pre_roll.copy()
            self._capture_onset = self._pre_roll.shape[1] + crossing
            self._capture_index = self.total_samples + crossing
            self._capture_floor = self.noise_floor_rms
            self._capture = np.concatenate([self._capture, channels], axis=1)
            ev = self._complete_if_ready()
            return [ev] if ev else []

        self._adapt(rms, warm_up=False)
        self._append_pre_roll(channels)
        return []

    def _adapt(self, measured_rms: float, warm_up: bool) -> None:
        if not math.isfinite(measured_rms):
            return
        measured = max(measured_rms, 1e-5)
        if warm_up:
            a = 0.14
            self.noise_floor_rms = (1 - a) * self.noise_floor_rms + a * measured
            return
        capped = min(measured, max(self.noise_floor_rms * 3.5, 0.020))
        a = 0.06 if capped > self.noise_floor_rms else 0.025
        self.noise_floor_rms = (1 - a) * self.noise_floor_rms + a * capped

    def _append_pre_roll(self, channels) -> None:
        pr = np.concatenate([self._pre_roll, channels], axis=1)
        self._pre_roll = pr[:, -self.pre_roll_samples:]

    def _complete_if_ready(self) -> Optional[DetectedTap]:
        cap = self._capture
        if cap is None or cap.shape[1] < self.window_samples:
            return None
        event = DetectedTap(
            channels=cap[:, :self.window_samples].copy(),
            onset_offset=min(self._capture_onset, self.window_samples - 1),
            stream_sample_index=self._capture_index,
            noise_floor_rms=self._capture_floor,
        )
        accepted = impact_gate_accepts(event, self.sample_rate,
                                       min_onset_contrast=1.4 if self.pattern_followup else 1.8)
        self._capture = None
        self._pre_roll = np.zeros((self.channel_count, 0), dtype=np.float32)
        # Rejected (speech-like) events keep the long pause so the noise floor can adapt.
        self._refractory = int(self.sample_rate * (self.refractory_after_tap if accepted else 0.14))
        self._adapt_in_refractory = not accepted
        return event if accepted else None


# ------------------------------------------------------------------- features

def _next_pow2(v: int) -> int:
    r = 1
    while r < v:
        r <<= 1
    return r


def power_spectrum(signal: np.ndarray, size: int) -> np.ndarray:
    """Hann-windowed power spectrum matching the Swift Radix2FFT."""
    size = _next_pow2(max(size, 2))
    buf = np.zeros(size)
    n = min(signal.size, size)
    if n > 1:
        idx = np.arange(n)
        window = 0.5 - 0.5 * np.cos(2 * np.pi * idx / (n - 1))
        buf[:n] = signal[:n] * window
    spec = np.fft.rfft(buf)
    return (spec.real ** 2 + spec.imag ** 2) / size


PASSIVE_FEATURE_NAMES = (
    ["log_rms", "crest_factor", "zero_crossing_rate", "attack_position", "temporal_centroid",
     "early_late_ratio", "spectral_centroid", "spectral_bandwidth", "spectral_rolloff_85",
     "spectral_flatness"]
    + [f"mfcc_{i}" for i in range(1, 9)]
    + [f"band_{i}" for i in range(10)]
    + ["channel_energy_delta", "interchannel_delay"]
)


class TapFeatureExtractor:
    def __init__(self, sample_rate: float):
        self.sample_rate = float(sample_rate)

    def extract(self, event: DetectedTap) -> TapFeature:
        channels = event.channels.astype(np.float64)
        mono = channels.mean(axis=0)
        spectrum = power_spectrum(mono, min(_next_pow2(mono.size), 4096))
        values = self._passive(mono, channels, event.onset_offset, spectrum)
        values = [v if math.isfinite(v) else 0.0 for v in values]

        rms = _rms(mono)
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        clipping = float(np.count_nonzero(np.abs(mono) >= 0.995)) / max(mono.size, 1)
        snr = 20 * math.log10(max(rms, 1e-12) / max(event.noise_floor_rms, 1e-12))
        quality = SignalQuality(snr, peak, rms, clipping, event.noise_floor_rms,
                                mono.size / self.sample_rate * 1000)
        return TapFeature(names=list(PASSIVE_FEATURE_NAMES), values=values, quality=quality)

    # -- helpers -----------------------------------------------------------
    def _bin(self, freq: float, count: int) -> int:
        fft_size = max((count - 1) * 2, 1)
        return min(max(int(freq / self.sample_rate * fft_size), 0), count - 1)

    def _passive(self, s: np.ndarray, channels: np.ndarray, onset: int, spec: np.ndarray) -> list[float]:
        n = s.size
        if n == 0:
            return [0.0] * len(PASSIVE_FEATURE_NAMES)
        rms = _rms(s)
        a = np.abs(s)
        peak = float(a.max())
        crest = peak / max(rms, 1e-12)
        signs = s >= 0
        zcr = float(np.count_nonzero(signs[1:] != signs[:-1])) / max(n - 1, 1)
        peak_index = int(np.argmax(a))
        attack = max(peak_index - onset, 0) / max(n - onset, 1)
        energies = s * s
        total = float(energies.sum()) + 1e-15
        tc = float(np.dot(np.arange(n), energies)) / total / max(n - 1, 1)
        split = min(max(onset + int(self.sample_rate * 0.025), 1), n - 1)
        early = float(energies[:split].sum())
        late = float(energies[split:].sum())
        elr = math.log10((early + 1e-12) / (late + 1e-12))
        temporal = [math.log10(rms + 1e-12), crest, zcr, attack, tc, elr]
        return (temporal + self._spectral(spec) + self._mfcc(spec, 18, 8)
                + self._bands(spec, 10) + self._spatial(channels))

    def _spectral(self, spec: np.ndarray) -> list[float]:
        sr = self.sample_rate
        denom = max((spec.size - 1) * 2, 1)
        freqs = np.arange(spec.size) * sr / denom
        mask = (freqs >= 60) & (freqs <= min(18000, sr * 0.48))
        f, p = freqs[mask], spec[mask]
        total = float(p.sum()) + 1e-15
        centroid = float(np.dot(f, p)) / total
        bandwidth = math.sqrt(float(np.dot((f - centroid) ** 2, p)) / total)
        cum = np.cumsum(p)
        hit = np.nonzero(cum >= total * 0.85)[0]
        rolloff = float(f[hit[0]]) if hit.size else 0.0
        count = max(p.size, 1)
        arithmetic = total / count
        geometric = math.exp(float(np.sum(np.log(p + 1e-15))) / count)
        flatness = geometric / max(arithmetic, 1e-15)
        return [centroid / sr, bandwidth / sr, rolloff / sr, flatness]

    def _mfcc(self, spec: np.ndarray, filters: int, coefs: int) -> list[float]:
        hz_to_mel = lambda hz: 2595 * math.log10(1 + hz / 700)
        mel_to_hz = lambda m: 700 * (10 ** (m / 2595) - 1)
        lo, hi = hz_to_mel(80), hz_to_mel(min(16000, self.sample_rate * 0.46))
        points = [mel_to_hz(lo + (hi - lo) * i / (filters + 1)) for i in range(filters + 2)]
        bins = [self._bin(p, spec.size) for p in points]
        log_e = np.zeros(filters)
        for k in range(filters):
            left = bins[k]
            center = max(bins[k + 1], left + 1)
            right = max(bins[k + 2], center + 1)
            e = 0.0
            if left < spec.size:
                for i in range(left, min(center, spec.size)):
                    e += spec[i] * (i - left) / max(center - left, 1)
                if center < spec.size:
                    for i in range(center, min(right, spec.size)):
                        e += spec[i] * (right - i) / max(right - center, 1)
            log_e[k] = math.log(e + 1e-15)
        idx = np.arange(filters) + 0.5
        return [float(np.dot(log_e, np.cos(np.pi * c * idx / filters))) / filters
                for c in range(1, coefs + 1)]

    def _bands(self, spec: np.ndarray, count: int) -> list[float]:
        lo, hi = 80.0, min(self.sample_rate * 0.46, 18000)
        total = float(spec.sum()) + 1e-15
        out = []
        for b in range(count):
            low = lo * (hi / lo) ** (b / count)
            high = lo * (hi / lo) ** ((b + 1) / count)
            start = self._bin(low, spec.size)
            end = max(start, self._bin(high, spec.size))
            e = float(spec[start:min(end, spec.size - 1) + 1].sum()) / total
            out.append(math.log10(e + 1e-12))
        return out

    def _spatial(self, channels: np.ndarray) -> list[float]:
        if channels.shape[0] < 2 or channels.shape[1] == 0:
            return [0.0, 0.0]
        first, second = channels[0], channels[1]
        delta = math.log10((_rms(first) + 1e-12) / (_rms(second) + 1e-12))
        max_lag = min(int(self.sample_rate * 0.00075), 36)
        best_lag, best = 0, -math.inf
        n = first.size
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                c = float(np.dot(first[:n - lag], second[lag:]))
            else:
                c = float(np.dot(first[-lag:], second[:n + lag]))
            if c > best:
                best, best_lag = c, lag
        return [delta, best_lag / max(max_lag, 1)]


# ------------------------------------------------------------------ classifier

MIN_CONFIDENCE = 0.36
SENSITIVITY_CONFIDENCE = {1: 0.60, 2: 0.48, 3: MIN_CONFIDENCE, 4: 0.32, 5: 0.28}
MIN_RELATIVE_SEPARATION = 0.035
MIN_LINEAR_MARGIN = 0.075


class TrainingError(Exception):
    pass


@dataclass
class Decision:
    zone: Optional[int]
    confidence: float
    signal_strength: float
    distances: list[float]
    rejection: Optional[str]

    @property
    def accepted(self) -> bool:
        return self.zone is not None and self.rejection is None


def _quantile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    pos = min(max(p, 0), 1) * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return s[lo]
    f = pos - lo
    return s[lo] * (1 - f) + s[hi] * f


class TapClassifier:
    """Ridge-regression zone model plus nearest-example novelty/negative gates.

    With a single calibrated zone there is nothing to tell apart, so the model only
    decides whether a sound is like the calibrated taps (novelty + negative gates).
    """

    def __init__(self, positives: list[LabeledTap], negatives: list[LabeledTap],
                 feature_names, center, scales, weights, outlier_threshold,
                 novelty_threshold, coefficients, zone_count, min_confidence=MIN_CONFIDENCE):
        self.positives = positives
        self.negatives = negatives
        self.zone_count = zone_count
        self.feature_names = list(feature_names)
        self.center = np.asarray(center, dtype=np.float64)
        self.scales = np.asarray(scales, dtype=np.float64)
        self.weights = np.asarray(weights, dtype=np.float64)
        self.outlier_threshold = float(outlier_threshold)
        self.novelty_threshold = None if novelty_threshold is None else float(novelty_threshold)
        self.coefficients = None if coefficients is None else np.asarray(coefficients, dtype=np.float64)
        self.min_confidence = min_confidence
        self._pos_norm = np.array([self._normalize(t.feature.values) for t in positives])
        self._pos_zones = np.array([int(t.zone) for t in positives])
        self._represented = sorted(set(int(z) for z in self._pos_zones))
        self._neg_norm = (np.array([self._normalize(t.feature.values) for t in negatives])
                          if negatives else np.zeros((0, len(self.center))))

    # -- training ----------------------------------------------------------
    @classmethod
    def train(cls, positives: list[LabeledTap], negatives: Optional[list[LabeledTap]] = None,
              zone_count: Optional[int] = None,
              min_confidence: float = MIN_CONFIDENCE) -> "TapClassifier":
        negatives = negatives or []
        positives = [p for p in positives if p.zone is not None]
        zones = sorted({int(p.zone) for p in positives})
        if not zones:
            raise TrainingError("No calibration taps.")
        zone_count = zone_count or (max(zones) + 1)
        if any(sum(1 for p in positives if p.zone == z) < 2 for z in zones):
            raise TrainingError("At least two examples per represented zone are required.")
        names = positives[0].feature.names
        if not names or any(t.feature.names != names or len(t.feature.values) != len(names)
                            for t in positives + negatives):
            raise TrainingError("All calibration examples must use the same feature schema.")

        X = np.array([p.feature.values for p in positives], dtype=np.float64)
        y = np.array([int(p.zone) for p in positives])
        center = np.median(X, axis=0)
        robust = np.median(np.abs(X - center), axis=0) * 1.4826
        std = X.std(axis=0, ddof=1) if X.shape[0] > 1 else np.zeros(X.shape[1])
        scales = np.maximum.reduce([robust, std * 0.35, np.full(X.shape[1], 1e-6)])
        N = (X - center) / np.maximum(scales, 1e-9)

        overall = N.mean(axis=0)
        between = np.zeros(N.shape[1])
        within = np.zeros(N.shape[1])
        for z in zones:
            Z = N[y == z]
            m = Z.mean(axis=0)
            between += Z.shape[0] * (m - overall) ** 2
            within += ((Z - m) ** 2).sum(axis=0)
        ratio = between / np.maximum(within, 1e-6)
        weights = np.clip(np.sqrt(ratio + 0.02), 0.12, 4.0)
        weights = weights / max(weights.mean(), 1e-9)

        nearest_w, nearest_u = [], []
        for i in range(N.shape[0]):
            same = [j for j in range(N.shape[0]) if j != i and y[j] == y[i]]
            if not same:
                continue
            d = N[same] - N[i]
            nearest_w.append(float(np.sqrt((d ** 2 @ weights) / max(weights.sum(), 1e-9)).min()))
            nearest_u.append(float(np.sqrt((d ** 2).mean(axis=1)).min()))
        outlier = max(_quantile(nearest_w, 0.95) * 2.6, 0.85)
        novelty = max(_quantile(nearest_u, 0.95) * 2.6, 0.95)

        coefficients = cls._train_linear(N, y, len(positives), zone_count) if len(zones) > 1 else None
        return cls(positives, negatives, names, center, scales, weights, outlier, novelty,
                   coefficients, zone_count, min_confidence)

    @staticmethod
    def _train_linear(N: np.ndarray, y: np.ndarray, count: int, zone_count: int):
        rows = np.hstack([np.ones((N.shape[0], 1)), N])
        targets = np.zeros((N.shape[0], zone_count))
        targets[np.arange(N.shape[0]), y] = 1.0
        A = rows.T @ rows
        reg = max(3.0, count * 0.25)
        A[1:, 1:] += np.eye(A.shape[0] - 1) * reg
        B = rows.T @ targets
        try:
            L = np.linalg.cholesky(A)
            sol = np.linalg.solve(L.T, np.linalg.solve(L, B))
        except np.linalg.LinAlgError:
            return None
        coef = sol.T  # zones x parameters
        return coef if np.all(np.isfinite(coef)) else None

    # -- prediction --------------------------------------------------------
    def _normalize(self, values) -> np.ndarray:
        return (np.asarray(values, dtype=np.float64) - self.center) / np.maximum(self.scales, 1e-9)

    def predict(self, feature: TapFeature, sensitivity: int = 3) -> Decision:
        q = feature.quality
        match = SENSITIVITY_MATCH.get(sensitivity, 1.0)
        min_confidence = SENSITIVITY_CONFIDENCE.get(sensitivity, self.min_confidence)
        outlier_threshold = self.outlier_threshold * match
        novelty_threshold = None if self.novelty_threshold is None else self.novelty_threshold * match

        def reject(reason, confidence=0.0, distances=None):
            return Decision(None, confidence, q.score, distances or [], reason)

        if feature.names != self.feature_names or len(feature.values) != self.center.size:
            return reject("schemaMismatch")
        if q.clipping > MAX_RELIABLE_CLIPPING:
            return reject("clippedSignal")
        if q.peak < MIN_RELIABLE_PEAK:
            return reject("weakSignal")
        if q.snr_db < MIN_CLASSIFICATION_SNR_DB:
            return reject("lowSignalToNoise")

        x = self._normalize(feature.values)
        diff = self._pos_norm - x
        weighted = np.sqrt((diff ** 2 @ self.weights) / max(self.weights.sum(), 1e-9))
        distances = [math.inf] * self.zone_count
        rank_w = [0.58, 0.28, 0.14]
        for z in self._represented:
            d = np.sort(weighted[self._pos_zones == z])[:3]
            w = rank_w[:d.size]
            distances[z] = float(np.dot(d, w) / sum(w))

        ranked = sorted([(d, i) for i, d in enumerate(distances) if math.isfinite(d)])
        best, best_zone = ranked[0]
        if best > outlier_threshold:
            return reject("outOfDistribution", distances=distances)

        nearest_pos = float(np.sqrt((diff ** 2).mean(axis=1)).min())
        if novelty_threshold is not None and nearest_pos > novelty_threshold:
            return reject("outOfDistribution", distances=distances)

        if self._neg_norm.shape[0]:
            nearest_neg = float(np.sqrt(((self._neg_norm - x) ** 2).mean(axis=1)).min())
            if nearest_neg <= nearest_pos * 1.10:
                return reject("resemblesNegativeExample", distances=distances)

        fit_score = min(max(1 - best / outlier_threshold, 0), 1)
        if len(ranked) == 1:
            # Single zone: passing the novelty and negative gates is the decision.
            return Decision(best_zone, 0.5 + 0.5 * fit_score, q.score, distances, None)

        linear = self._linear(x, nearest_pos, distances, q, min_confidence, novelty_threshold or outlier_threshold)
        if linear is not None:
            return linear

        second = ranked[1][0]
        separation = (second - best) / max(second, 1e-9)
        sep_score = min(max(separation / 0.38, 0), 1)
        confidence = 0.72 * sep_score + 0.28 * fit_score
        if separation < MIN_RELATIVE_SEPARATION or confidence < min_confidence:
            return reject("ambiguousZone", confidence, distances)
        return Decision(best_zone, confidence, q.score, distances, None)

    def _linear(self, x, nearest_pos, distances, q, min_confidence, fit_threshold) -> Optional[Decision]:
        if self.coefficients is None or self.coefficients.shape != (self.zone_count, x.size + 1):
            return None
        inp = np.concatenate([[1.0], x])
        scores = sorted(((float(self.coefficients[z] @ inp), z) for z in self._represented), reverse=True)
        scores = [s for s in scores if math.isfinite(s[0])]
        if len(scores) < 2:
            return None
        margin = scores[0][0] - scores[1][0]
        margin_score = min(max(margin / 0.30, 0), 1)
        fit_score = min(max(1 - nearest_pos / fit_threshold, 0), 1)
        confidence = 0.76 * margin_score + 0.24 * fit_score
        if margin < MIN_LINEAR_MARGIN or confidence < min_confidence:
            return None
        return Decision(scores[0][1], confidence, q.score, distances, None)


def leave_one_out(positives: list[LabeledTap], zone_count: int) -> tuple[float, list[float]]:
    """Returns (overall accuracy, per-zone accuracy) using held-out calibration taps."""
    positives = [p for p in positives if p.zone is not None]
    if len(positives) < 4:
        raise TrainingError("Not enough samples.")
    correct = [0] * zone_count
    total = [0] * zone_count
    for i, held in enumerate(positives):
        model = TapClassifier.train(positives[:i] + positives[i + 1:], zone_count=zone_count)
        d = model.predict(held.feature)
        total[held.zone] += 1
        if d.zone == held.zone:
            correct[held.zone] += 1
    per_zone = [c / t if t else 0.0 for c, t in zip(correct, total)]
    return sum(correct) / max(sum(total), 1), per_zone


# ------------------------------------------------------------- tap patterns

PATTERN_NAMES = ["Single tap", "Double tap", "Triple tap"]
MAX_PATTERN_TAPS = 3
PATTERN_GAP_SECONDS = 0.6


class TapPatternTracker:
    """Groups accepted taps in the same zone into single / double / triple taps.

    A pattern fires as soon as it cannot grow any more: when it reaches three taps,
    when no longer pattern has an action for that zone (so a plain single tap fires
    with no added delay), or when the next tap does not arrive within the gap.
    """

    def __init__(self, wants_more, gap: float = PATTERN_GAP_SECONDS, max_taps: int = MAX_PATTERN_TAPS):
        self._wants_more = wants_more        # (zone, taps_so_far) -> bool
        self.gap = gap
        self.max_taps = max_taps
        self._zone: Optional[int] = None
        self._count = 0
        self._last = 0.0

    @property
    def last_tap_at(self) -> float:
        return self._last

    @property
    def pending(self) -> Optional[tuple[int, int]]:
        return None if self._zone is None else (self._zone, self._count)

    def add(self, zone: int, at: float) -> list[tuple[int, int]]:
        fired = []
        if self._zone is not None and (zone != self._zone or at - self._last > self.gap):
            fired.append((self._zone, self._count))
            self._zone = None
        if self._zone is None:
            self._zone, self._count = zone, 1
        else:
            self._count += 1
        self._last = at
        if self._count >= self.max_taps or not self._wants_more(zone, self._count):
            fired.append((self._zone, self._count))
            self._zone = None
        return fired

    def poll(self, now: float) -> list[tuple[int, int]]:
        if self._zone is not None and now - self._last > self.gap:
            fired = [(self._zone, self._count)]
            self._zone = None
            return fired
        return []

    def reset(self) -> None:
        self._zone = None
