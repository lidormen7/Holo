# SPDX-License-Identifier: MIT
# Part of Holo for Windows, a port of Holo by JustinGamer191 (https://github.com/JustinGamer191/Holo).
"""Microphone capture on Windows via PortAudio (sounddevice), replacing AVAudioEngine."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .core import StreamingTapDetector, TapFeatureExtractor, TapFeature, DetectedTap

BLOCK_FRAMES = 512


@dataclass
class Observation:
    feature: TapFeature
    detected_at: float          # time.perf_counter() when the audio block arrived
    noise_floor: float


def list_input_devices() -> list[tuple[int, str]]:
    import sounddevice as sd
    devices = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] <= 0:
            continue
        api = sd.query_hostapis(dev["hostapi"])["name"]
        devices.append((index, f"{dev['name']}  [{api}]"))
    return devices


def default_input_name() -> Optional[str]:
    import sounddevice as sd
    try:
        info = sd.query_devices(kind="input")
        api = sd.query_hostapis(info["hostapi"])["name"]
        return f"{info['name']}  [{api}]"
    except Exception:
        return None


class AudioCaptureService:
    """Runs the input stream, detector and feature extraction off the UI thread."""

    def __init__(self, on_observation: Callable[[Observation], None],
                 on_error: Callable[[str], None]):
        self._on_observation = on_observation
        self._on_error = on_error
        self._stream = None
        self._queue: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue(maxsize=400)
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self.sample_rate = 48000.0
        self.channels = 1
        self.device_label: Optional[str] = None
        self.detector: Optional[StreamingTapDetector] = None
        self.level = 0.0  # recent RMS for the level meter
        self._detector_settings: dict = {}

    def configure_detector(self, **settings) -> None:
        """Detector attributes (loudness, refractory_after_tap, pattern_followup) that are
        safe to change while listening; kept and re-applied when the stream restarts."""
        self._detector_settings.update(settings)
        if self.detector is not None:
            for name, value in settings.items():
                setattr(self.detector, name, value)

    def set_loudness(self, multiplier: float) -> None:
        """How much louder than normal a tap must be to be heard (sensitivity setting)."""
        self.configure_detector(loudness=multiplier)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, device_label: Optional[str] = None) -> None:
        import sounddevice as sd
        self.stop()
        device = None
        if device_label:
            for index, label in list_input_devices():
                if label == device_label:
                    device = index
                    break
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self.sample_rate = float(info["default_samplerate"]) or 48000.0
        self.channels = max(1, min(int(info["max_input_channels"]), 2))
        self.device_label = device_label or default_input_name()
        self.detector = StreamingTapDetector(self.sample_rate, self.channels)
        for name, value in self._detector_settings.items():
            setattr(self.detector, name, value)
        extractor = TapFeatureExtractor(self.sample_rate)

        def callback(indata, frames, time_info, status):
            try:
                self._queue.put_nowait((indata.copy(), time.perf_counter()))
            except queue.Full:
                pass  # drop rather than block the audio thread

        self._stream = sd.InputStream(
            device=device, channels=self.channels, samplerate=self.sample_rate,
            blocksize=BLOCK_FRAMES, dtype="float32", callback=callback, latency="low",
        )
        self._running = True
        self._worker = threading.Thread(target=self._run, args=(extractor,), daemon=True)
        self._worker.start()
        self._stream.start()

    def _run(self, extractor: TapFeatureExtractor) -> None:
        detector = self.detector
        while self._running:
            try:
                block, arrived = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.level = 0.8 * self.level + 0.2 * float(np.sqrt(np.mean(block ** 2)))
                events: list[DetectedTap] = detector.process(block)
                for event in events:
                    feature = extractor.extract(event)
                    self._on_observation(Observation(feature, arrived, event.noise_floor_rms))
            except Exception as exc:  # keep listening after one bad block
                self._on_error(f"Audio processing error: {exc}")

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._worker is not None:
            self._worker.join(timeout=1)
            self._worker = None
        while not self._queue.empty():
            self._queue.get_nowait()
