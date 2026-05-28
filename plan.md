# Sonos Beside — plan

This project is already set up for:

- A **config-driven list** of launchable items (`controller/config.json` with `items[]`)
- A local **config web UI** at `/ui` and JSON endpoints (`/items`, `/browse`, `/reload`)
- A runtime loop testable on **macOS** (keyboard) and on **Pi** (GPIO encoders + Sharp SPI display)

---

## Equipment to buy

Rough US-style street prices; shop around.

**Current dev setup (2026):** Pi Zero W v1.1 + **Adafruit Sharp #3502** (1.3" 144×168). **Target:** Pi Zero 2 WH + Sharp #4694 (2.7" 400×240).

| Part | Source | ~Price |
|------|--------|--------|
| **Raspberry Pi Zero 2 W** (with **pre-soldered GPIO header** — often sold as **Zero 2 WH**) — *upgrade path when in stock* | Adafruit / Pimoroni / authorized resellers | ~$15 |
| **Adafruit Sharp Memory Display 2.7"** 400×240 — **product #4694** specifically — *target; often OOS* | [adafruit.com](https://www.adafruit.com) | ~$25 |
| **Smaller Sharp** (e.g. 2.13" 250×122 or 1.3" — **match Adafruit wiring guide for that SKU**) | Adafruit | ~$20–25 |
| **Bourns PEC11R** rotary encoder **with switch** (push) ×2 | Mouser / DigiKey | ~$8 |
| **Premium knob caps** ×2 (e.g. Nanu Arc or similar) | nanu.design / Amazon | ~$20 |
| **MicroSD card** 16 GB+ (Class 10 / A1 fine) | Amazon | ~$8 |
| **Power supply** — **USB-C 5 V / 2.5 A** for Zero 2 W; **Pi Zero W v1.1** uses **micro-USB** (still aim for a decent 5 V / 2+ A supply) | Amazon | ~$10 |
| **4× 10 kΩ resistors** (pull-ups for encoder quadrature / switch if not using internal pull-ups everywhere) | Amazon / Mouser | ~$1 |
| **Dupont jumpers** + small **perfboard** (clean wiring, not a rat’s nest in the box) | Amazon / Adafruit | ~$5 |

**Electronics subtotal: ~$92**

### Enclosure (pick one)

| Option | Effort | ~Cost |
|--------|--------|-------|
| Bamboo/wood **project box** (Etsy / AliExpress) + drill for screen + knobs | Low | ~$20 |
| **3D-printed** shell + wood veneer film | Medium | ~$25 |
| **Hand-cut** walnut (or similar) from scratch | High | ~$20 materials |

**Grand total (device + box): ~$110–120** depending on enclosure.

### Ordering gotchas

1. **Pi Zero 2 W with headers** — look for **Zero 2 WH** or explicit “with header soldered” so you skip soldering the 40-pin yourself. **Pi Zero W (v1.1)** is fine to start: same 40-pin GPIO and SPI0; it is **slower** and has **512 MB RAM** — watch memory if you add heavy services. Power is **micro-USB**, not USB-C. **Headers:** a bare Zero W needs a **soldered 40-pin male header** (or buy a **v1.1 with header** if in stock and you want to skip that step — same board, convenience only).
2. **Sharp display** — Adafruit has several Sharp panels; **#4694** is the 2.7" 400×240 **target** for this stack. Smaller panels use different resolutions and sometimes different **CS/DC/RST** pinouts — follow the **product page wiring** and align `display.py` dimensions.
3. **Encoders** — get **PEC11R with integrated push switch** (e.g. suffixes like **-0020F-S0018** vary; on Mouser/DigiKey filter for “with switch” / pushbutton). Detents + nice knob caps = the “actuation feel” you want.

### Pi ↔ peripherals wiring (matches `controller/main.py`)

**Wire colors:** white CLK · grey DT · black SW · brown encoder 1 GND · purple encoder 2 GND · blue display GND · orange 3.3V · red display CLK · yellow display DI · green display CS

**Encoder 1 — playlist scroll + select**

| Encoder pin | Wire | Pi | BCM GPIO | Physical pin |
|-------------|------|-----|----------|--------------|
| A (CLK) | White | GPIO | 17 | 11 |
| B (DT) | Grey | GPIO | 27 | 13 |
| SW | Black | GPIO | 22 | 15 |
| C (common) | Brown | **GND** | — | 6 |
| SW (other leg) | Brown | **GND** | — | 9 |

**Encoder 2 — volume + play/pause**

| Encoder pin | Wire | Pi | BCM GPIO | Physical pin |
|-------------|------|-----|----------|--------------|
| A (CLK) | White | GPIO | 5 | 29 |
| B (DT) | Grey | GPIO | 26 | 37 |
| SW | Black | GPIO | 13 | 33 |
| C (common) | Purple | **GND** | — | 34 |
| SW (other leg) | Purple | **GND** | — | 39 |

**GPIO vs physical header pins** — code uses **BCM GPIO numbers**, not physical pin positions. See [pinout.xyz](https://pinout.xyz/) for the full map.

**Typical 5-pin encoder layout** (PEC11R-style — verify your part):

```
[ A ] [ C ] [ B ]     ← rotation (A=CLK, B=DT, C=GND)
   [ SW ] [ SW ]       ← switch (one → SW GPIO, one → GND)
```

Wire **C** and one **SW** leg to Pi **GND**. Use male-to-female jumpers: **female → Pi header**, **male → breadboard** in the same row as the encoder leg.

**Sharp display (SPI)** — silkscreen: **EIN · DISP · EMD · CS · DI · CLK · GND · 3v3 · VIN**

| Display pin | Wire | Pi | BCM GPIO | Physical pin |
|-------------|------|-----|----------|--------------|
| VIN | Orange | 3.3V | — | 1 |
| DISP | Orange | 3.3V | — | 17 |
| GND | Blue | GND | — | 25 |
| EMD | Blue | GND | — | 20 |
| CLK | Red | GPIO | 11 | 23 |
| DI | Yellow | GPIO | 10 | 19 |
| CS | Green | GPIO | 6 | 31 |
| EIN, 3v3 | — | NC | — | — |

Full table in [README.md](README.md#hardware-wiring). Display **CS = GPIO 6 (pin 31)**. Encoder 2 **DT = GPIO 26 (pin 37)**.

### Network / config from a Mac

On the Pi, run the Flask app (port **8080** in current code). Use **mDNS** so the box is reachable as something like **`http://sonos-box.local:8080/ui`** to edit playlists without USB. Same JSON API works from `curl` or scripts.

---

## Chosen control mapping (two encoders only)

No extra skip button: **long-press on the volume/play-pause encoder** = **next track (skip)**. Single short press = play/pause as today.

**Room presets:** **long-press the playlist encoder** to enter **room change mode**; **long-press the same encoder again** to exit back to normal (playlist browse / now-playing flow). While in room mode, **rotate** that encoder to move through presets; **short press** to confirm the highlighted preset as active (exact confirm gesture can match whatever feels best in hardware testing).

| Encoder | Rotate | Short press | Long press |
|---------|--------|-------------|------------|
| **A (playlist)** | Scroll list / in room mode: scroll presets | Load & play selected item / confirm preset | Toggle **room change mode** on ↔ off |
| **B (volume)** | Volume up/down | Play / pause | **Skip** (next track) |

**Implementation notes**

- Use a small **press state machine**: debounce, **long-press threshold** (~600–800 ms — tune on hardware), release handling so short vs long doesn’t misfire.
- On-screen **hold feedback** (bar or “Hold… skip” / “Rooms”) so long-press actions never feel ambiguous.

---

## Room presets: what “select rooms” means

- Playback targets a **Sonos group**; presets are named groups of **rooms/players** (e.g. “All”, “Bedroom”, “Kitchen + Living + Bath”).
- Same idea as playlists: **editable in config** and ideally in `/ui` later.

### Proposed config shape (JSON)

```json
{
  "active_room_preset": "all",
  "room_presets": [
    { "id": "all", "label": "All rooms", "rooms": ["Kitchen", "Living Room", "Bedroom"] },
    { "id": "downstairs", "label": "Downstairs", "rooms": ["Kitchen", "Living Room"] },
    { "id": "bedroom", "label": "Bedroom", "rooms": ["Bedroom"] }
  ],
  "items": [
    { "type": "favorite", "id": "11", "label": "GET HAPPY!" }
  ]
}
```

- `rooms[]` should resolve to stable Sonos identifiers (names or player IDs from discovery) once implemented.
- **Persist** `active_room_preset` across reboots unless you explicitly want “session only.”

---

## Screen UX: now playing vs browse

**Default:** **Now playing** (or idle) view — room preset, play state, volume, track title/artist when available.

**Browse:** Rotating **encoder A** opens or refreshes the **playlist list** overlay; after **~6–10 s** without A activity, fall back to now playing.

**States (conceptual)**

- `NOW_PLAYING` — idle home screen  
- `BROWSE_LIST` — playlist list; A rotates selection, A short press loads  
- `ROOM_CHOOSER` — entered/exited by **long-press A**; A rotates among presets, short press confirms  
- Toasts — errors, “Preset: Downstairs”, volume tick, etc.

**Encoder B** — volume and play/pause should not force leaving browse unless you decide otherwise; long-press B = skip, with visible hold progress.

**Footer hint (example):** `Hold A: rooms · Hold B: skip`

---

## Configuration UX (Mac-first, Pi later)

Extend `/ui` (and JSON API) the same way as playlists:

- Discover rooms, edit `room_presets`, set `active_room_preset`
- Endpoints like `GET /rooms`, `GET/PUT /room_presets`, `POST /active_room_preset`, plus `/reload`

---

## Future upgrades (not required for v1)

- **Recessed bezel + LED strip** around the Sharp LCD for a soft glow (separate power/layout planning; Sharp is reflective — lighting is aesthetic, not backlight).
- **Jellyfin → Sonos** is a separate ecosystem: native paths are messier than first-party music; options include community **Jellyfin Sonos / SMAPI**-style plugins (often need HTTPS reachable to speakers), **DLNA** paths (format quirks), or **Plex** if you want the smoothest official Sonos integration. Treat as **future work**, not part of this box’s core scope.

---

## Summary

| Topic | Decision |
|-------|----------|
| Hardware core | **Now:** Pi Zero W v1.1 + smaller Sharp; **Target:** Pi Zero 2 WH, Sharp #4694 — 2× PEC11R w/ switch, knobs, SD, supply, passives + perfboard |
| Skip | **Long-press** encoder B (play/pause knob) |
| Rooms | **Long-press** encoder A to enter room mode; **long-press A again** to exit; rotate to choose, short press to confirm |
| Screen | Default **now playing**; playlist browse as **timeout overlay** |
| Editing lists | **`/ui` on LAN** (+ optional USB/Ethernet same network) |
