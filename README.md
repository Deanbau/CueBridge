<p align="center">
  <img src="assets/icon.png" width="120" alt="CueBridge">
</p>

# CueBridge

OSC-to-switcher bridge for live theatre and broadcast. Translates OSC messages from show control systems (QLab, ETC Eos, etc.) into native commands for video switchers.

**Supported switchers:** Barco Event Master · PixelHue · Blackmagic ATEM

---

## Features

- Web UI — configure and monitor from any browser on the LAN
- Multi-switcher — control several switchers simultaneously from one OSC stream
- Cue engine — map OSC addresses to preset recalls, cuts, takes, and macros
- mDNS advertising — CueBridge announces itself on the local network
- Hardware simulator — test cue maps without physical switchers
- Runs headless on Raspberry Pi, Windows, or macOS

## Quick Start

```bash
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8080** in your browser.

## OSC Mapping

| OSC Address | Action |
|---|---|
| `/cuebridge/preset <id>` | Recall preset by ID |
| `/cuebridge/cut` | Cut to program |
| `/cuebridge/take` | Take (auto-transition) |
| `/cuebridge/macro <id>` | Fire macro by ID |

Custom mappings are configured in the **Cues** tab of the web UI.

## Default Ports

| Port | Protocol | Purpose |
|---|---|---|
| 8080 | TCP | Web UI |
| 9000 | UDP | OSC listener |

Both ports are configurable.

## Documentation

- [Installation & Run Guide](INSTALL.md) — Python install, systemd service, Task Scheduler, PyInstaller binaries
- [Building Binaries](INSTALL.md#packaging-as-a-standalone-executable-pyinstaller) — standalone executables via PyInstaller

## Building a Standalone Binary

```bash
# macOS / Linux
bash build.sh

# Windows
build.bat
```

Output: `dist/cuebridge` (macOS/Linux) or `dist\cuebridge.exe` (Windows).

## License

CueBridge is free software released under the GNU General Public License v3.0 or later.  
See [LICENSE](LICENSE) for the full text.
