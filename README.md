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

## Hardware wiring

Encoder wiring (KY-040 breakouts, `+` left NC) is in [`plan.md`](plan.md). GPIO numbers below are **BCM** — see [pinout.xyz](https://pinout.xyz/) for the physical header map.

**Wire colors (this build):**

| Color | Use |
|-------|-----|
| White | Encoder CLK |
| Grey | Encoder DT |
| Black | Encoder SW |
| Brown | Encoder 1 ground |
| Purple | Encoder 2 ground |
| Blue | Display ground (GND, EMD) |
| Orange | Power / 3.3V (display VIN, DISP) |
| Red | Display CLK |
| Yellow | Display DI |
| Green | Display CS |

### Rotary encoders — KY-040 breakout modules

**Parts:** [KY-040](https://www.amazon.com/dp/B07T3672VK) (or equivalent) — 5-pin header **CLK · DT · SW · + · GND**. Bourns PEC11R sits on the module; the PCB handles the switch and optional onboard pull-ups.

**This build:** **4 duponts per encoder** — CLK, DT, SW, GND. Leave **`+` unconnected** (Pi internal pull-ups in `encoder.py` are enough; avoids needing a third 3.3V header pin when the display uses pins 1 and 17). **Never** tie `+` to GND.

Duponts: **female → Pi header**, **male → KY-040 holes**.

| Encoder | KY-040 pin | Wire | Pi | BCM GPIO | Physical pin |
|---------|------------|------|-----|----------|--------------|
| 1 — playlist | CLK | White | GPIO | 17 | 11 |
| 1 — playlist | DT | Grey | GPIO | 27 | 13 |
| 1 — playlist | SW | Black | GPIO | 22 | 15 |
| 1 — playlist | GND | Brown | **GND** | — | 6 |
| 1 — playlist | + | — | **NC** | — | — |
| 2 — volume | CLK | White | GPIO | 5 | 29 |
| 2 — volume | DT | Grey | GPIO | 26 | 37 |
| 2 — volume | SW | Black | GPIO | 13 | 33 |
| 2 — volume | GND | Purple | **GND** | — | 34 |
| 2 — volume | + | — | **NC** | — | — |

*(GND physical pins are interchangeable — any Pi GND pin works.)*

Requires `pigpiod` running (`sudo systemctl start pigpiod`).

**Bench test on the Pi:**

```bash
cd controller
python encoder_probe.py          # one line per detent
python encoder_probe.py --raw    # CLK/DT pin levels while turning
```

### Sharp memory display — Adafruit #3502 (1.3" 144×168)

**Your panel:** [Adafruit #3502](https://www.adafruit.com/product/3502) — 144×168 portrait monochrome Sharp memory display. Planned upgrade: 2.7" **#4694** (400×240).

Your breakout silkscreen: **EIN · DISP · EMD · CS · DI · CLK · GND · 3v3 · VIN**

#### Display → Raspberry Pi

| Display pin | Wire | Connect to | BCM GPIO | Physical pin |
|-------------|------|------------|----------|--------------|
| **VIN** | Orange | 3.3V | — | 1 |
| **DISP** | Orange | 3.3V *(display on)* | — | 17 |
| **GND** | Blue | Ground | — | 25 |
| **EMD** | Blue | Ground *(required)* | — | 20 |
| **CLK** | Red | SPI clock | 11 | 23 |
| **DI** | Yellow | SPI MOSI | 10 | 19 |
| **CS** | Green | Chip select | 6 | 31 |
| **3v3** | — | *(leave unconnected)* | — | — |
| **EIN** | — | *(leave unconnected)* | — | — |

Use **separate Pi pins** for each wire — e.g. two orange wires to pin 1 and pin 17 (both 3.3V), two blue wires to pin 20 and pin 25 (both GND). Same voltage, different holes.

**Power:** Use **VIN → Pi 3.3V** (not 5V on a Pi). Do **not** tie both VIN and 3v3 to the Pi — **3v3** is an *output* from the breakout regulator when fed from 5V; on the Pi you feed **VIN** only.

**Control pins:**
- **DISP → 3.3V** turns the panel on (blank if left floating)
- **EMD → GND** selects software VCOM mode — required for the Adafruit driver
- **EIN** (ExtComIn) — not connected in this mode

| Not used | Notes |
|----------|-------|
| **MISO** | Display is write-only |
| **3v3** | Output pin — leave unconnected when powering via VIN from Pi |
| **EIN** | NC when EMD is tied to GND |

Wiring matches `controller/sharp_hw.py`. Display **CS = GPIO 6 (pin 31)**. Encoder 2 **DT = GPIO 26 (pin 37)** — keeps CS and DT off the same pin.

#### Before first test

1. Enable SPI: `sudo raspi-config` → Interface Options → SPI → Enable → reboot
2. Confirm: `ls /dev/spidev0.0` *(must exist or the app stays in simulated/terminal mode)*
3. Power **off** the Pi while plugging jumpers

#### Resolution in code

Defaults to **144×168** (#3502). For the 2.7" **#4694** (400×240):

```bash
DISPLAY_WIDTH=400 DISPLAY_HEIGHT=240 python main.py
```

#### Bench test with dupont jumpers only

No solder or breadboard required for a quick test if your breakout has **through-hole pads**:

| Jumper end | Goes to |
|------------|---------|
| **Female** | Pi header — see wire/color table above |
| **Male** | Push into the matching hole on the display breakout |

Hold the board still — friction-fit connections pop out easily. Promote to breadboard or soldered header when you're done iterating.

#### How to know it's working

| Startup message | Meaning |
|-----------------|---------|
| `[display] Simulated display ready` | SPI not enabled or display not detected — still using terminal output |
| `[display] Sharp 144×168 ready` | Hardware display path active |
| Blank panel | Re-seat jumpers; check VIN, DISP, GND, EMD, CLK, DI, CS |
| Static / snow | Run the pattern test (below) |

#### Display pattern test (diagnose static)

```bash
sudo systemctl stop sonos
cd ~/sonos-bedside-controller/controller
python display_test.py
```

Cycles blank → solid fill → border → stripes → text every 3s. Terminal prints which pattern is active — tell which (if any) looks correct.

If still static, try on the Pi:

```bash
DISPLAY_SPI_HZ=500000 python display_test.py
DISPLAY_WIDTH=168 DISPLAY_HEIGHT=144 python display_test.py   # try swapped if portrait looks wrong
DISPLAY_INVERT=0 python display_test.py   # only if white/black look reversed
```

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| `Missing SONOS_CLIENT_ID` | Copy `.env` to the repo root on the Pi |
| `KeyError: access_token` or token refresh error | Re-run `python auth_setup.py` on your Mac, re-copy `controller/tokens.json` |
| Encoders dead / bounce / false select | `sudo systemctl status pigpiod`; run `python encoder_probe.py`. KY-040: **GND + CLK/DT/SW only**, `+` NC, **never +→GND**. Wire encoder 2 or floating GPIO 5/26/13 can spuriously change volume/play |
| Test on Pi without encoders wired | `USE_KEYBOARD=1 python main.py` over SSH (same keys as Mac) |
| Test keyboard with SPI enabled but no display | `SIMULATE_DISPLAY=1 USE_KEYBOARD=1 python main.py` |
| Display static / snow | Wrong panel size? Try `DISPLAY_WIDTH=144 DISPLAY_HEIGHT=168` (1.3") or slower SPI: `DISPLAY_SPI_HZ=1000000`. Check DISP→3.3V and EMD→GND. |
| White/black reversed | Invert is on by default; try `DISPLAY_INVERT=0` if colors look wrong |
| Display blank | SPI enabled? `ls /dev/spidev0.0`. See [Hardware wiring → Sharp display](#sharp-memory-display--smaller-panel-250122) |
| Can't reach `:8080` from Mac | Pi and Mac on same WiFi? Try `ping sonos-box.local` |
