# Notice: origin of Holo for Windows

Holo for Windows is a **derivative work** of [Holo](https://github.com/JustinGamer191/Holo)
by **JustinGamer191 and Holo contributors**. It is a separate, unofficial port: the original
project targets macOS and is not responsible for this port or its bugs.

- **License:** MIT, the same license as the original. See [`LICENSE`](LICENSE). It keeps the
  original copyright notice, as the MIT License requires, and adds one for the port.
- **Permission:** the original author was asked and approved publishing this port.
- **Based on:** the upstream repository as of commit `87c36ae`.

## What comes from the original

| Original (Swift, macOS) | Windows port (Python) |
|---|---|
| `Sources/HoloCore/StreamingTapDetector.swift` | `holo/core.py` `StreamingTapDetector`: same thresholds and adaptation rules |
| `Sources/HoloCore/ImpactEventGate.swift` | `holo/core.py` `impact_gate_accepts` / `impact_metrics` |
| `Sources/HoloCore/TapFeatureExtractor.swift`, `FFT.swift` | `holo/core.py` `TapFeatureExtractor`, `power_spectrum` |
| `Sources/HoloCore/TapClassifier.swift` | `holo/core.py` `TapClassifier` (ridge zone model, novelty and negative-example gates) |
| `Sources/HoloCore/SignalModels.swift`, `GuidedCaptureQuality.swift`, `Evaluation.swift` | signal quality, calibration guidance, leave-one-out check, accuracy test |
| `DESIGN.md`, `Sources/HoloApp/DeskMapView.swift` | the interface direction: calm surfaces, one accent color, side-rail desk map |

## What is new in the port

- Windows audio capture (PortAudio via `sounddevice`) instead of AVAudioEngine.
- A tkinter interface with light/dark mode, a tray icon, start with Windows, and a single running instance.
- Windows actions: keyboard shortcuts, `cmd.exe`/PowerShell, Snipping Tool, `winsound`, System.Speech.
- 1–4 zone layouts (including left / right / in front of the laptop) and single/double/triple tap actions.
- A tap-sensitivity setting, and shorter detector pauses on the Desk so quick double taps are heard.
- Calibration required per PC and microphone, an offline-friendly install, and a PyInstaller build.
- The app icon was drawn for this port.

Not ported: the experimental ultrasonic "active probe", the diagnostics view and debug WAV capture.
