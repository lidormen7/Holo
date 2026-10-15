# Third-party software

This repository contains only Holo's own source code. The packages below are installed by
`pip` from `requirements.txt` and keep their own licenses. License information was taken
from each package's published metadata (versions as tested).

## Runtime dependencies

| Package | Version tested | License | Project |
|---|---|---|---|
| numpy | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) | https://numpy.org |
| sounddevice | 0.5.6 | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio (DLL inside sounddevice) | bundled with sounddevice 0.5.6 | PortAudio license (MIT-style) | http://www.portaudio.com |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| pycparser | 3.11 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pystray | 0.19.5 | **LGPL-3.0** | https://github.com/moses-palmer/pystray |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |
| Pillow | 12.3.0 | MIT-CMU (HPND) | https://python-pillow.github.io |
| sv-ttk (Sun Valley theme) | 2.6.1 | MIT | https://github.com/rdbende/Sun-Valley-ttk-theme |

## Build-only tools (used by `build_exe.bat`, not imported by Holo)

| Package | Version tested | License |
|---|---|---|
| PyInstaller | 6.22.3 | GPL-2.0-or-later **with a bootloader exception** that allows distributing the built `Holo.exe` under any license |
| pyinstaller-hooks-contrib | 2026.8 | Apache-2.0 / GPL-2.0 (hooks only) |
| altgraph | 0.17.5 | MIT |
| pefile | 2023.2.7 | MIT |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| setuptools | 84.0.0 | MIT |

## If you distribute `Holo.exe`

The single-file executable built by `build_exe.bat` bundles Python and the runtime packages
above. When sharing it:

1. Include Holo's [`LICENSE`](LICENSE) and this file.
2. Include the license texts of the bundled packages (each package ships its license inside
   its wheel's `*.dist-info` folder), plus Python's own license (PSF License).
3. **pystray is LGPL-3.0:** state that the executable contains pystray, include the LGPL-3.0 and
   GPL-3.0 texts, and point to where its source is available (link above). People can rebuild the
   executable with a modified pystray from this repository's source with `build_exe.bat`.
