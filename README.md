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
# optional: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET (see Configuring playlists / favorites)
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

Keyboard: `j`/`k` music, Enter load, `,`/`.` rooms, `a` apply preset, `[`/`]` skip, Space play/pause, `+`/`-` volume, `m` mute. Config UI: http://localhost:8080/ui

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

Install the unit file once (symlink so `git pull` updates it — adjust `User` and paths if yours differ):

```bash
sudo ln -sf ~/sonos-bedside-controller/deploy/sonos.service /etc/systemd/system/sonos.service
sudo nano ~/sonos-bedside-controller/deploy/sonos.service   # fix User / paths if needed
```

(`cp` works too, but then you have two copies to keep in sync.)

Or paste manually:

```ini
[Unit]
Description=Sonos Bedside Controller
After=network-online.target pigpiod.service
Wants=network-online.target
Requires=pigpiod.service

[Service]
Type=simple
User=beth
WorkingDirectory=/home/beth/sonos-bedside-controller/controller
EnvironmentFile=-/home/beth/sonos-bedside-controller/.env
ExecStart=/home/beth/venv/bin/python main.py
Restart=always
RestartSec=10
StartLimitIntervalSec=0
TimeoutStopSec=15

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

> **Don't run `python main.py` in an SSH session for production.** That only lives while you're logged in. Use the systemd service above so it starts on boot and survives disconnects.

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
sudo systemctl daemon-reload      # only if deploy/sonos.service changed
sudo systemctl restart sonos
```

`git pull` won't touch `.env` or `controller/tokens.json`.

---

## Configuring playlists / favorites / room presets

Edit `controller/config.json` by hand, or use the web UI at **http://sonos-box.local:8080/ui** on your LAN. After saving changes in the UI, the running app picks them up via `/reload`.

The UI supports drag-to-reorder, ▶ preview on Sonos, ✎ custom labels, and **room presets** (named room lists). `active_room_preset` is persisted; the ROOMS knob scrolls presets and press applies them (`createGroup`).

```bash
curl -s http://sonos-box.local:8080/rooms | jq
curl -s http://sonos-box.local:8080/room_presets | jq
```

### Spotify “Top Tracks” and artist names

Sonos favorites like **Top Tracks** all share the same title. Sonos’s API only puts `"Spotify Playlist"` in the subtitle — not the artist. The app works around that in two ways:

1. **Optional Spotify API creds (recommended)** — add to `.env` on Mac and Pi:

   ```bash
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   ```

   Create a free app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). Client-credentials flow only; no user login or redirect URI. On browse/load, the app reads the Spotify artist ID embedded in each favorite and resolves the name (e.g. “Sorry Darling” under “Top Tracks”). Results are cached in `controller/artist_cache.json` (gitignored).

2. **Without Spotify creds** — press ▶ on a favorite once in the UI (or select it on the device). The app reads now-playing metadata from Sonos, caches the artist, and shows it on the next page reload. You can also rename items manually with ✎.

After changing `.env`, restart the service (`sudo systemctl restart sonos` on the Pi).

---

## Hardware wiring

Encoder wiring is in [`plan.md`](plan.md).

### Rotary encoders — Adafruit I2C breakout + Bourns PEC11R

**Parts:** [Adafruit I2C Quad Rotary Encoder Breakout #5752](https://www.adafruit.com/product/5752) with Bourns PEC11R-4215F-S0024 encoders (populate all four slots). The seesaw chip handles quadrature decoding and debounce in hardware — Pi reads encoder position and button state over I2C.

**4 dupont wires total** for the whole bank:

| Board pad | Pi | Physical pin |
|-----------|-----|--------------|
| **VIN** | 5V | 2 |
| **-** (GND) | GND | 6 |
| **D** (SDA) | SDA1 | 3 |
| **C** (SCL) | SCL1 | 5 |

I2C address: **0x49** (default). Interim slot map (seesaw switch pins):

| Slot | Switch pin | Role | Turn | Press |
|------|------------|------|------|-------|
| **0** | 12 | **MUSIC** | scroll favorites | load & play |
| **1** | 14 | **ROOMS** | scroll room presets | apply preset |
| **2** | 17 | **VOL** | volume | mute / unmute |
| **3** | 9 | **SKIP** | prev / next track | play / pause |

Requires I2C enabled (`sudo raspi-config` → Interface Options → I2C).

**Bench test on the Pi:**

```bash
sudo i2cdetect -y 1              # should show 0x49
cd controller
python encoder_probe.py          # all 4 slots; one line per detent
python encoder_probe.py --raw    # position + button state
```

### Encoder + display architecture

The seesaw chip on the Adafruit breakout counts detents in hardware, so there is no risk of the Pi missing edges during slow SPI redraws. `encoder.py` polls the seesaw over I2C in the main loop.

```
  PEC11R detent
       │
       ▼
  seesaw chip (hardware decode)   ← handles quadrature + debounce on-chip
       │
       ▼
  encoder.py (I2C poll)           ← reads position delta + button state
       │
      ├─ rotate → scroll music/rooms / volume / skip
      └─ press  → queue select / apply / mute / play_pause
       │
       ▼
  main loop every ~20ms: process_encoder_ui()
       ├─ if list dirty  → music or rooms list (one pane)
       ├─ elif vol dirty → render_volume_adjust(...)  ← shows target + mute
       └─ run queued button action (Sonos HTTP)
       │
       ▼
  volume Sonos API: absolute set toward target every VOLUME_FLUSH_S,
                    capped by VOLUME_MAX_STEP / VOLUME_SESSION_CAP / VOLUME_MAX
```

| Piece | File | Role |
|-------|------|------|
| Detent decode | seesaw hardware | On-chip quadrature decode; position counter increments per detent |
| Input state | `main.py` `scroll()` / `volume()` | `selected` and volume target updated under `_input_lock` |
| Display | `main.py` `process_encoder_ui()` | Paints **latest** state; may skip frames while spinning fast — selection is still correct |
| Bench test | `encoder_probe.py` | Same I2C read as production; no display |

Mac / `USE_KEYBOARD=1`: same actions, immediate `_paint_*` (no I2C).

**Tuning:**

```bash
# Display redraw rate:
LIST_RENDER_MIN_S=0.05 python main.py      # max ~20 playlist paints/sec while spinning
LIST_RENDER_IDLE_S=0.04 python main.py     # always paint once after you stop

# Volume — local target is instant; Sonos trails with rate limits (won't deafen on a fast crank)
VOLUME_MAX=70 python main.py              # hard ceiling (default 70)
VOLUME_SESSION_CAP=12 python main.py      # max +% per continuous spin before idle unlock
VOLUME_SESSION_IDLE_S=0.35 python main.py # pause to open another +SESSION_CAP window
VOLUME_FLUSH_S=0.08 python main.py        # how often Sonos is nudged toward target
VOLUME_MAX_STEP=4 python main.py          # max % change per flush tick
VOLUME_LEAD_MAX=8 python main.py          # target can't run more than this % ahead of Sonos
VOLUME_PCT_UP=1 VOLUME_PCT_DOWN=2 python main.py
# VOLUME_PCT_PER_DETENT=2 still works as a legacy override for both directions
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
| App only runs while SSH'd in | You're starting it manually — set up systemd (§8). `systemctl is-enabled sonos` should say `enabled`. |
| App doesn't start on power-on | `journalctl -u sonos -b` — usually WiFi not ready yet or pigpiod down. Startup now retries every 10s until Sonos responds. Check `systemctl status pigpiod`. |
| Display shows happy face / app "died" | Process exited cleanly (systemd stop, crash, or old SSH session). `journalctl -u sonos --since today` for the reason. With systemd + `Restart=always` it should come back in ~10s. |
| App stops after hours/days | Check `journalctl -u sonos` for OOM (`Killed`) or Sonos token errors. Reload failures no longer kill the main loop. |
| `Missing SONOS_CLIENT_ID` | Copy `.env` to the repo root on the Pi |
| `KeyError: access_token` or token refresh error | Re-run `python auth_setup.py` on your Mac, re-copy `controller/tokens.json` |
| Encoders dead / not detected | `sudo i2cdetect -y 1` — should show `0x49`. Check VIN (pin 2), GND (pin 6), SDA (pin 3), SCL (pin 5). Run `python encoder_probe.py` to test counting. |
| Probe OK but menu/volume barely moves in app | Old bug: SPI in GPIO callback. Ensure current `main.py` uses `process_encoder_ui()` — see [Encoder + display architecture](#encoder--display-architecture) |
| Test on Pi without encoders wired | `USE_KEYBOARD=1 python main.py` over SSH (same keys as Mac) |
| Test keyboard with SPI enabled but no display | `SIMULATE_DISPLAY=1 USE_KEYBOARD=1 python main.py` |
| Display static / snow | Wrong panel size? Try `DISPLAY_WIDTH=144 DISPLAY_HEIGHT=168` (1.3") or slower SPI: `DISPLAY_SPI_HZ=1000000`. Check DISP→3.3V and EMD→GND. |
| White/black reversed | Invert is on by default; try `DISPLAY_INVERT=0` if colors look wrong |
| Display blank | SPI enabled? `ls /dev/spidev0.0`. See [Hardware wiring → Sharp display](#sharp-memory-display--smaller-panel-250122) |
| Can't reach `:8080` from Mac | Pi and Mac on same WiFi? Try `ping sonos-box.local` |
