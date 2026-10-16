# Holo for Windows

**Tap your desk. Your PC responds.**

> **An unofficial Windows port of [Holo](https://github.com/JustinGamer191/Holo) by JustinGamer191.**
> The original is a macOS app; this folder is a Python port of its tap-detection and classification
> core with a new Windows interface, published with the original author's approval under the same
> MIT License. See [NOTICE.md](NOTICE.md) for what was ported and what is new,
> and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency licenses.
> Please report Windows-port issues here, not to the original project.

Holo listens through your laptop's microphone and splits the desk around the laptop into tap zones.
It recognizes which zone you tapped (and whether it was a single, double or triple tap) and runs the
action you assigned to it.

```
                 Screen side
   Left     ┌──────────────┐    Right
            │    Laptop    │
            └──────────────┘
          Front (between you and the keyboard)
```

## Install and run

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/. In the installer, tick **"Add python.exe to PATH"**.
2. Double-click **`run_holo.bat`**. The first run takes a minute to set up. After that it opens straight away.
3. If Windows asks for microphone access, allow it. Also check **Settings → Privacy & security → Microphone →
   "Let desktop apps access your microphone"** is on.

### A single `Holo.exe` for PCs without Python

Run **`build_exe.bat`** on a PC that has Python. It builds `dist\Holo.exe`, checks it with a self-test
(look for `SELF-TEST PASSED`), and that one file runs on any 64-bit Windows 10/11 PC with nothing installed.
Copy only `Holo.exe`; the `.venv` folder does not work on another PC.

The exe is not code-signed, so Windows SmartScreen may warn the first time: click *More info → Run anyway*.

## First run: calibration is required

Every PC, microphone and desk sounds different, so Holo opens on the **Calibrate** page. The Desk and
Accuracy test pages unlock only after you calibrate and save. If you select a different microphone later, or the
microphone's sample rate changes, Holo locks again and asks you to recalibrate. Your actions are kept.

## Using it

1. **Calibrate.** Choose the **number of zones** (1–4):
   - 1 zone: anywhere around the laptop (left, right or in front). Most reliable.
   - 2 zones: left / right of the laptop.
   - 3 zones: left, right, and in front of the laptop (between you and the keyboard).
   - 4 zones: the four corners (like the original).

   Put the laptop where it will stay and keep the room quiet. Press *Begin calibration* and tap
   (single taps) in each highlighted zone until its counter is full, spread around the zone with a short pause.
   Every zone in a layout gets the same number of taps: 20 for 1 zone, 12 for 2–3 zones (bigger areas),
   10 for 4 zones.
   Holo tells you if a tap was too soft, too loud or masked by noise.
   At the end it reports a consistency score. If it is below 80%, use *Redo zone* on the weakest zone, then *Save profile*.
2. Optional but recommended (especially with 1 zone): under **Non-tap sounds**, press **Record for 12 s** and
   talk, type and touch the laptop. Holo learns to ignore those sounds.
3. **Actions.** Pick a zone, then choose what a **single, double and triple tap** on it does.
   Double/triple tap = tap the same zone 2–3 times with about a quarter to half a second between taps.
   If a zone only has a single-tap action it fires instantly; otherwise Holo waits ~0.6 s for more taps.
   Changes save immediately, and the *Test* button runs the action once.
   - Play sound: a Windows system sound or a `.wav` file
   - Copy text to the clipboard / Speak text aloud
   - Open a website
   - Open an app, file or folder (`.exe`, `.lnk` shortcut, document, folder)
   - Run a command (`cmd.exe`) or a PowerShell script
   - Press a keyboard shortcut, e.g. `ctrl+shift+esc`, `win+d`, `playpause`, `volumemute`, `alt+f4`
     (keys can be in any order; a misspelled key is shown in red under the field as you type)
   - Screenshot the whole screen to the clipboard, or snip a region with the Snipping Tool

   If an action fails, the reason appears at the bottom of the window. It clears when you edit the action,
   or after 10 seconds.
4. **Desk.** Tap the desk. The recognized zone lights up and its action runs.
   Actions run while the Desk page is open **or while Holo is in the background**.
5. **Accuracy test** (optional). Tap each zone 15 times when asked. Holo shows how many taps it recognized
   correctly and which zones it confused. Aim for 80% or better; if a zone scores low, recalibrate.

If you move the laptop, change desks, or switch microphones, recalibrate. The sound path changes.

## Settings

- **Tap sensitivity** (Strict · Careful · Balanced · Responsive · Sensitive). Stricter settings need a louder tap
  to be heard *and* a closer match to your calibration before an action runs, so random noises are ignored.
  Calibration and the accuracy test always listen at Balanced so profiles stay comparable.
- **Appearance**: System (follows Windows), Light or Dark.
- **Microphone** with a live input level. Changing it needs a new calibration.
- **Start with Windows** and **Run actions**.

## Running in the background

Closing the window does not stop Holo. It keeps listening with an icon next to the clock, like an antivirus agent.
Right-click that icon for *Open Holo*, *Listening*, *Run actions*, **Start with Windows**, and *Exit Holo*.
Starting Holo a second time just opens the running copy.

Holo is not a Windows *service* on purpose. Services run in an isolated session without access to your
microphone permission, screen, clipboard or keyboard, so the actions could not work from there.

## Offline / internal-network PCs

Installing only runs `pip` (no other downloads), so a PC whose `pip.ini` points at an internal package server works as-is.
For a PC with no network at all, run `download_wheels.bat` on a connected PC with the same Python version, then copy
the whole folder (including `wheels\`). `run_holo.bat` and `build_exe.bat` then install from that folder with no network.
At runtime Holo makes no network connections (only actions you assign, like opening a website, touch the network).

## Where data is stored

`%APPDATA%\Holo\profile.json` holds tap *features* (numbers), never audio recordings, plus your zone and action settings. `settings.json` holds app preferences.
Accuracy test results are saved next to it as text files. Nothing is sent over the network.

## What changed from the macOS version

| macOS original | Windows port |
|---|---|
| Swift / SwiftUI app | Python + tkinter |
| AVAudioEngine | PortAudio via `sounddevice` |
| HoloCore DSP (detector, impact gate, features, classifier) | Ported to NumPy with the same thresholds and math (no SciPy needed) |
| Requires the built-in MacBook mic | Any microphone you pick (built-in laptop mic recommended) |
| Run Shortcut, `/bin/zsh`, `screencapture`, NSSound, AVSpeech | Keyboard shortcut, `cmd.exe` / PowerShell, Snipping Tool, `winsound`, System.Speech |
| Active / Hybrid ultrasonic probe (experimental) | Not ported. Only passive sensing, which the original recommends by default |
| Diagnostics view, debug WAV capture, approach comparison | Not ported |

Profiles from the Mac app are not compatible. Calibrate again on Windows.

## Development

```
python -m pip install -r requirements.txt pytest
python -m pytest tests
python -m holo
```

## License

MIT. See [LICENSE](LICENSE), which keeps the original Holo copyright notice and adds one for this port.
Third-party packages keep their own licenses: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
