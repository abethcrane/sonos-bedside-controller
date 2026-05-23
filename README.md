# Sonos Bedside Controller

A Raspberry Pi controller for Sonos — rotary encoders, optional Sharp memory display, and a local web UI for editing playlists/favorites.

Repo: [github.com/abethcrane/sonos-bedside-controller](https://github.com/abethcrane/sonos-bedside-controller)

---

## Mac setup (do this first)

### 1. Clone the repo

```bash
git clone https://github.com/abethcrane/sonos-bedside-controller.git
cd sonos-bedside-controller
```

### 2. Sonos API credentials

1. Create an app at [developer.sonos.com](https://developer.sonos.com) (Control API, `playback-control-all` scope).
2. Set the redirect URI to `http://localhost:8888/callback`.
3. Copy `.env.example` → `.env` and fill in your client id/secret:

```bash
cp .env.example .env
# edit .env — SONOS_CLIENT_ID and SONOS_CLIENT_SECRET
```

### 3. Python venv + one-time OAuth

Creates `controller/tokens.json` (also gitignored):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python auth_setup.py
```

A browser tab opens; log in to Sonos. When it finishes, `controller/tokens.json` is written.

> **`.env` and `controller/tokens.json` are gitignored.** Never commit them. You'll copy them to the Pi once during setup below.

### 4. Test on Mac (optional)

```bash
source venv/bin/activate
cd controller
python main.py
```

Keyboard: `j`/`k` scroll, Enter select, Space play/pause, `+`/`-` volume. Config UI: http://localhost:8080/ui

---

## Raspberry Pi setup

Tested on **Raspberry Pi OS Lite (64-bit)**. No desktop required.

### 1. Flash the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Insert your SD card. Choose:

- **OS:** Raspberry Pi OS Lite (64-bit)
- Click the **gear icon** before flashing and set:
  - **hostname:** `sonos-box` (or whatever you like)
  - **username / password**
  - **WiFi:** your network name + password
  - **Enable SSH:** yes

Flash, insert the card, power on, wait ~60 seconds.

### 2. SSH in from your Mac

```bash
ssh beth@sonos-box.local
```

(Replace `beth` with your Pi username if different.)

### 3. Install system dependencies

```bash
sudo apt update && sudo apt install -y \
  python3-pip python3-venv python3-dev git \
  pigpio python3-pigpio

sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

If you have a **Sharp SPI display** wired up, also enable SPI:

```bash
sudo raspi-config
# Interface Options → SPI → Enable
```

Without SPI, the app still runs — it simulates the display in the terminal until the panel is connected.

### 4. Clone the repo

On the **Pi**:

```bash
cd ~
git clone https://github.com/abethcrane/sonos-bedside-controller.git
```

### 5. Copy secrets from your Mac (one-time)

`git clone` does not include `.env` or `controller/tokens.json`. From your **Mac**:

```bash
scp ~/code/sonos-bedside-controller/.env beth@sonos-box.local:~/sonos-bedside-controller/
scp ~/code/sonos-bedside-controller/controller/tokens.json beth@sonos-box.local:~/sonos-bedside-controller/controller/
```

(Adjust the Mac path if you cloned somewhere else.)

### 6. Python venv + install dependencies

Back on the **Pi**:

```bash
cd ~
python3 -m venv venv
source venv/bin/activate
pip install -r ~/sonos-bedside-controller/requirements.txt
```

### 7. Test it runs

```bash
source ~/venv/bin/activate
cd ~/sonos-bedside-controller/controller
python main.py
```

No encoders wired yet? Use keyboard mode over SSH:

```bash
USE_KEYBOARD=1 python main.py
```

You should see `Connecting to Sonos...` and `Config server running on http://localhost:8080`. Ctrl+C to stop.

From your Mac, open **http://sonos-box.local:8080/ui** — if the config page loads, you're good.

### 8. Auto-start on boot

```bash
sudo nano /etc/systemd/system/sonos.service
```

Paste (adjust `User` and paths if yours differ):

```ini
[Unit]
Description=Sonos Controller
After=network-online.target pigpiod.service
Wants=network-online.target

[Service]
User=beth
WorkingDirectory=/home/beth/sonos-bedside-controller/controller
ExecStart=/home/beth/venv/bin/python main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable sonos
sudo systemctl start sonos
sudo systemctl status sonos
```

Logs: `journalctl -u sonos -f`

---

## Deploying updates

**Mac** — push your changes:

```bash
git add -A && git commit -m "your message" && git push
```

**Pi** — pull and restart:

```bash
cd ~/sonos-bedside-controller
git pull
source ~/venv/bin/activate
pip install -r requirements.txt   # only if dependencies changed
sudo systemctl restart sonos
```

`git pull` won't touch `.env` or `controller/tokens.json`.

---

## Configuring playlists / favorites

Edit `controller/config.json` by hand, or use the web UI at **http://sonos-box.local:8080/ui** on your LAN. After saving changes in the UI, the running app picks them up via `/reload`.

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| `Missing SONOS_CLIENT_ID` | Copy `.env` to the repo root on the Pi |
| `KeyError: access_token` or token refresh error | Re-run `python auth_setup.py` on your Mac, re-copy `controller/tokens.json` |
| Encoders dead | `sudo systemctl status pigpiod` — daemon must be running |
| Test on Pi without encoders wired | `USE_KEYBOARD=1 python main.py` over SSH (same keys as Mac) |
| Display blank | Check SPI wiring and `raspi-config` SPI enable; see `plan.md` for GPIO pinout |
| Can't reach `:8080` from Mac | Pi and Mac on same WiFi? Try `ping sonos-box.local` |
