# CueBridge — Installation & Run Guide

CueBridge is a Python application. There is nothing to compile — you install Python, install the dependencies, and run it. The web UI is served from the machine running CueBridge and accessed from any browser on the same network.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.11 or newer |
| pip | bundled with Python |

---

## macOS

### 1. Install Python

Download the macOS installer from [python.org](https://www.python.org/downloads/) and run it, **or** use [Homebrew](https://brew.sh):

```bash
brew install python@3.12
```

Verify:

```bash
python3 --version
```

### 2. Clone or download the project

```bash
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
```

### 3. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python main.py
```

Open **http://localhost:8080** in your browser.

#### Optional flags

```bash
# Different web UI port
python main.py --port 9090

# Custom config file location
python main.py --config /path/to/my_config.json

# Don't open a browser window automatically
python main.py --headless

# Reduce log verbosity
python main.py --log-level INFO
```

#### Run the hardware simulator (no physical switchers needed)

```bash
python simulator.py
```

Open **http://localhost:8090** in your browser to see the simulator dashboard.

---

## Windows

### 1. Install Python

Download the installer from [python.org](https://www.python.org/downloads/windows/).

During installation, **tick "Add Python to PATH"** before clicking Install.

Verify in a new Command Prompt or PowerShell window:

```powershell
python --version
```

### 2. Clone or download the project

Using Git:

```powershell
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
```

Or download and extract the ZIP from GitHub.

### 3. Create a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 4. Install dependencies

```powershell
pip install -r requirements.txt
```

### 5. Run

```powershell
python main.py
```

Open **http://localhost:8080** in your browser. A browser window will open automatically.

#### Optional flags

```powershell
python main.py --port 9090
python main.py --config C:\path\to\my_config.json
python main.py --headless
python main.py --log-level INFO
```

#### Run the hardware simulator

```powershell
python simulator.py
```

Open **http://localhost:8090**.

#### Run on startup (Windows Task Scheduler)

1. Open **Task Scheduler** → Create Basic Task.
2. Trigger: **At log on** (or At startup using the SYSTEM account).
3. Action: **Start a program**
   - Program: `C:\path\to\CueBridge\.venv\Scripts\python.exe`
   - Arguments: `main.py --headless`
   - Start in: `C:\path\to\CueBridge`
4. In the task Properties → General, tick **"Run whether user is logged on or not"**.

---

## Raspberry Pi

Tested on Raspberry Pi 4 and Pi 5 running **Raspberry Pi OS (64-bit, Bookworm)**.

### 1. Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Install Python 3.11+

Bookworm ships with Python 3.11. Verify:

```bash
python3 --version
```

If you have an older OS version and need a newer Python:

```bash
sudo apt install -y python3.11 python3.11-venv python3.11-pip
```

### 3. Clone or download the project

```bash
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
```

### 4. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 5. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** On a Pi 3 or older, the first install can take several minutes while pip builds native extensions. A Pi 4/5 finishes in under a minute.

### 6. Run (headless — no monitor needed)

```bash
python main.py --headless
```

Access the UI from any device on the same network:

```
http://<pi-ip-address>:8080
```

Find the Pi's IP with:

```bash
hostname -I
```

#### Run automatically on boot (systemd)

1. Create the service file:

```bash
sudo nano /etc/systemd/system/cuebridge.service
```

Paste the following, adjusting the paths to match your username and install location:

```ini
[Unit]
Description=CueBridge OSC Switcher Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/CueBridge
ExecStart=/home/pi/CueBridge/.venv/bin/python main.py --headless --log-level INFO
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

2. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cuebridge
sudo systemctl start cuebridge
```

3. Check it is running:

```bash
sudo systemctl status cuebridge
```

4. View live logs:

```bash
journalctl -u cuebridge -f
```

5. Stop or restart:

```bash
sudo systemctl stop cuebridge
sudo systemctl restart cuebridge
```

---

## Docker

Works on macOS, Linux, Windows, and Raspberry Pi (arm64).

### Prerequisites

Install [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/).

### Build and run

```bash
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
docker compose up -d
```

Open **http://localhost:8080** (or `http://<host-ip>:8080` from another device).

The config file is volume-mounted from the host so settings persist across container restarts and updates.

### View logs

```bash
docker compose logs -f
```

### Stop

```bash
docker compose down
```

### Update

```bash
git pull
docker compose build --pull
docker compose up -d
```

### Raspberry Pi

Same steps. The `python:3.12-slim` base image is multi-arch and pulls the correct arm64 layer automatically.

```bash
# On the Pi:
git clone https://github.com/Deanbau/CueBridge.git
cd CueBridge
docker compose up -d
```

To auto-start on boot, Docker's `restart: unless-stopped` policy handles it — no systemd unit needed.

> **Network note:** `docker-compose.yml` uses `network_mode: host` so OSC UDP and mDNS work without extra port-mapping configuration. On macOS Docker Desktop, host networking is not supported; switch to the `ports:` block in `docker-compose.yml` instead.

### Cross-build for Pi from macOS (optional)

```bash
docker buildx build --platform linux/arm64 -t cuebridge:arm64 --load .
```

---

## Firewall / Network Notes

CueBridge binds to **all interfaces (0.0.0.0)** so it is reachable from other machines on the LAN without extra configuration.

| Port | Protocol | Purpose |
|---|---|---|
| 8080 | TCP | Web UI (configurable with `--port`) |
| 8000 | UDP | OSC listener (configurable in the OSC Settings tab) |

If your machine has a firewall enabled, allow inbound traffic on both ports:

**macOS:**
System Settings → Network → Firewall → Options → add `python3` or allow the ports explicitly.

**Windows:**
Windows Defender Firewall → Advanced Settings → New Inbound Rule → Port → UDP 8000 and TCP 8080.

**Linux / Raspberry Pi (ufw):**
```bash
sudo ufw allow 8080/tcp
sudo ufw allow 8000/udp
```

---

## Packaging as a standalone executable (PyInstaller)

PyInstaller bundles Python and all dependencies into a single file. No Python installation needed on the target machine.

### Install PyInstaller

```bash
pip install pyinstaller
```

### Build

```bash
pyinstaller --onefile --name cuebridge main.py
```

The executable is written to `dist/cuebridge` (macOS/Linux) or `dist\cuebridge.exe` (Windows).

#### NiceGUI static files

NiceGUI ships its own web assets. PyInstaller does not bundle them automatically. Add this flag to include them:

```bash
pyinstaller --onefile --name cuebridge \
  --add-data "$(python -c 'import nicegui; import pathlib; print(pathlib.Path(nicegui.__file__).parent)'):nicegui" \
  main.py
```

On Windows (PowerShell):

```powershell
$niceguiPath = python -c "import nicegui; import pathlib; print(pathlib.Path(nicegui.__file__).parent)"
pyinstaller --onefile --name cuebridge --add-data "$niceguiPath;nicegui" main.py
```

### Run the packaged binary

```bash
# macOS / Linux
./dist/cuebridge --headless

# Windows
dist\cuebridge.exe --headless
```

All command-line flags (`--port`, `--config`, `--log-level`, `--headless`) work the same as the Python version.

### Notes

- The `cuebridge_config.json` file is read/written relative to the working directory where you run the binary, not where it lives.
- The binary is not cross-platform: build on macOS to get a macOS binary, build on Windows for Windows, etc.
- Raspberry Pi: the Python method is simpler and recommended for Pi. Use the systemd service approach instead.

---

## Updating

```bash
cd CueBridge
git pull
source .venv/bin/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

Then restart the app (or the systemd service on Pi).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python: command not found` | Use `python3` instead, or re-install Python with PATH option ticked |
| UI shows but OSC does nothing | Check the OSC port is not blocked by a firewall; confirm the sending app is targeting the right IP and port |
| `Address already in use` on startup | Another process is using port 8080 — change it with `--port` |
| Switcher shows ERROR status | Verify the IP address in the switcher config; check the switcher is powered on and reachable with `ping <ip>` |
| Presets list is empty | Click **Refresh** on the switcher card after it connects |
| Pi reboots and service doesn't start | Add `After=network-online.target` is already in the unit file — ensure `systemd-networkd-wait-online` is enabled: `sudo systemctl enable systemd-networkd-wait-online` |
