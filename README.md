# Holo for Windows

Turn the desk next to your laptop into buttons. Tap the desk, and Windows runs the command you chose.

Holo for Windows listens through your laptop's microphone. It learns how taps on *your* desk sound,
works out where you tapped, and runs an action: open an app or website, press a keyboard shortcut,
run a command, take a screenshot and more.

> Based on [Holo](https://github.com/JustinGamer191/Holo) by JustinGamer191, a macOS app, and published
> with the author's approval under the MIT License. This repository is a fork: the Windows app is in
> [`windows/`](windows/), and the original macOS project files are kept unchanged alongside it.
> Details: [windows/NOTICE.md](windows/NOTICE.md).

## Highlights

- **1 to 4 tap zones:** anywhere on the desk; left / right; left / right / in front of the laptop; or four corners.
- **Single, double and triple taps:** up to three actions per zone.
- **Windows actions:**
  - open an app, file, folder or website
  - press a keyboard shortcut (`win+d`, `ctrl+shift+esc`, media keys…)
  - run a `cmd` or PowerShell command
  - copy or speak text, play a sound
  - take a screenshot or open the Snipping Tool
- **Runs in the background** with an icon next to the clock, and can start with Windows.
- **Tap sensitivity** from Strict to Sensitive, so stray noises don't trigger anything.
- **Light and dark mode**, following Windows or set by hand.
- **Private by design:** everything runs on your PC. No audio is recorded or saved, and nothing is sent anywhere.
- **Works offline:** installation only needs `pip`, or a folder of packages copied from another PC.

## Get started

**Requirements:** Windows 10 or 11 (64-bit) and a microphone (a laptop's built-in one works best).

1. Download this repository (**Code → Download ZIP**) and unzip it.
2. Install [Python](https://www.python.org/downloads/) 3.10 or newer, and tick **"Add python.exe to PATH"**.
3. Open the `windows` folder and double-click **`run_holo.bat`**. The first run installs what it needs.
4. **Calibrate:** choose how many zones you want and tap each highlighted zone until its counter fills up.
   Every PC and microphone sounds different, so Holo asks for this on first run.
5. **Actions:** pick what each zone does for one, two or three taps.
6. Close the window. Holo keeps listening next to the clock.

**No Python on the target PC?** Run `windows\build_exe.bat` once on any PC that has Python. It produces a
single `Holo.exe` that runs anywhere, with no installation.

Full guide (also in Hebrew), settings, troubleshooting and offline install: **[windows/README.md](windows/README.md)**

## How it works

1. **Detect:** a streaming onset detector waits for a short, sharp sound and ignores speech and steady background noise.
2. **Describe:** about 90 ms of audio around the tap becomes a set of acoustic features (timing, spectrum, MFCCs).
3. **Recognize:** a classifier trained on your calibration taps picks the zone, or rejects the sound if it's unsure.
4. **Act:** single, double or triple taps on that zone run the action you assigned.

The detection and classification math is ported from the original Holo's Swift core, with the same thresholds.

## Repository layout

| Path | What it is |
|---|---|
| [`windows/`](windows/) | **Holo for Windows**: Python source, tests, install and build scripts |
| `.github/workflows/windows-build.yml` | Builds and tests `Holo.exe` on GitHub Actions |
| everything else | The original macOS app by JustinGamer191, unchanged ([upstream](https://github.com/JustinGamer191/Holo)) |

## Credits and license

- Original Holo (macOS) © 2026 JustinGamer191 and Holo contributors.
- Windows port © 2026 lidormen7.
- Released under the **MIT License**: free to use, change and share, as long as the copyright and license
  notice stay included. See [windows/LICENSE](windows/LICENSE), and [LICENSE](LICENSE) for the original project.
- Third-party packages keep their own licenses: [windows/THIRD_PARTY_NOTICES.md](windows/THIRD_PARTY_NOTICES.md).

Issues with the Windows version belong here, not in the original project.
