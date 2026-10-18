# Sonos Beside — plan

This project is already set up for:

- A **config-driven list** of launchable items (`controller/config.json` with `items[]`)
- A local **config web UI** at `/ui` and JSON endpoints (`/items`, `/browse`, `/reload`)
- A runtime loop testable on **macOS** (keyboard) and on **Pi** (I2C encoders + display)

**Product target (locked):** 4 labeled knobs, no long-press, HDMI color display above the dials, room **presets**, soft on-screen labels aligned to each knob.

**Current bench (interim):** Pi Zero W v1.1 + Adafruit Sharp #3502 (1.3" 144×168) + 2 encoders on Adafruit #5752. Keep shipping iterative UX on this while parts arrive.

---

## Equipment to buy

Rough US-style street prices; shop around.

**Target:** Pi Zero 2 WH + **no-touch HDMI** 5" (or 4–5" IPS HDMI used without touch) + **4×** Bourns on Adafruit #5752 + wood box.

| Part | Source | ~Price |
|------|--------|--------|
| **Raspberry Pi Zero 2 W** (with **pre-soldered GPIO header** — **Zero 2 WH**) | Adafruit / Pimoroni / authorized resellers | ~$15 |
| **Display (primary):** [Adafruit HDMI 4 Pi: 5" (no Touch) #1928](https://www.adafruit.com/product/1928) — 800×480, HDMI driver board, USB 5V, **no touch** — *often OOS; check DigiKey/Mouser* | Adafruit | ~$60 |
| **Display (fallback):** Waveshare **4"** or **5"** HDMI IPS (800×480 or 480×800 landscape) — connect **HDMI only**; do **not** wire/enable touch | Waveshare / Pi Hut / Amazon | ~$40–55 |
| **Display (alt embed):** Eyoyo-style 5" mini HDMI monitor (no touch) — easier to buy, metal chassis harder to flush-mount | Amazon | ~$50–60 |
| **Adafruit I2C Quad Rotary Encoder Breakout #5752** + **Bourns PEC11R-4215F-S0024** ×4 (all four slots) | Adafruit | ~$25–35 |
| **Premium knob caps** ×4 (e.g. Nanu Arc or similar) | nanu.design / Amazon | ~$40 |
| **MicroSD** 16 GB+ (Class 10 / A1) | Amazon | ~$8 |
| **Power** — USB-C **5 V / 2.5 A+** for Zero 2 W (display may need its own USB 5V) | Amazon | ~$10–15 |
| **Mini-HDMI** cable/adapter (Zero → panel) + duponts / perfboard | Amazon / Adafruit | ~$10 |

**Electronics subtotal: ~$160–200** depending on display stock and knobs.

### Enclosure (pick one)

| Option | Effort | ~Cost |
|--------|--------|-------|
| Bamboo/wood **project box** + cutout for screen above, 4 knob holes below | Low–med | ~$25–40 |
| **3D-printed** shell + wood veneer | Medium | ~$25 |
| **Hand-cut** walnut (or similar) | High | ~$20+ materials |

Layout: **screen on top, four knobs in a row under it** so on-screen soft labels line up with each dial.

### Ordering gotchas

1. **Pi Zero 2 WH** — headers pre-soldered. Zero has **no DSI**; use **HDMI** (mini-HDMI). Interim Zero W v1.1 is fine for software (slower, micro-USB power).
2. **Prefer no-touch panels** for a knob-only box. Waveshare HDMI kits usually include touch — leave it disconnected. Avoid **SPI** PiTFTs (slow list scroll — why we're leaving Sharp). Avoid **DPI/HyperPixel** (eats GPIO; fights I2C encoders).
3. **Night dimming** — plan PWM backlight (many Waveshare panels: pad → GPIO 18). Adafruit #1928 has OSD buttons for brightness; confirm software dim path when integrating.
4. **Encoders** — populate all four slots on #5752. Seesaw @ **0x49** over I2C; 4 wires for the whole bank.

### Pi ↔ peripherals wiring

**Encoders — [Adafruit #5752](https://www.adafruit.com/product/5752)** (I2C), four Bourns PEC11R:

| Board pad | Pi | Physical pin |
|-----------|-----|--------------|
| **VIN** | 5V | 2 |
| **-** (GND) | GND | 6 |
| **D** (SDA) | SDA1 | 3 |
| **C** (SCL) | SCL1 | 5 |

I2C address: **0x49**. Slot map (target):

| Slot | Role |
|------|------|
| **#0** | **ROOMS** — scroll presets / press apply |
| **#1** | **MUSIC** — scroll favorites / press load & play |
| **#2** | **VOL** — volume / press mute·unmute |
| **#3** | **SKIP** (transport) — prev·next / press play·pause |

**Bench test:** `sudo i2cdetect -y 1` → `0x49`, then `python encoder_probe.py` (extend probe for four slots).

**Display — HDMI** (not SPI Sharp). Power per panel docs (often separate USB 5V). Mini-HDMI from Zero. **GPIO stays free** for backlight PWM + I2C encoders.

**Interim Sharp SPI wiring** (current bench only) — see [README.md](README.md#hardware-wiring); CS = GPIO 6. Do not treat Sharp as the product target.

### Network / config from a Mac

Flask on port **8080**. Prefer mDNS: **`http://sonos-box.local:8080/ui`**. Same JSON API via `curl`.

---

## Chosen control mapping (four encoders, no long-press)

No long-press. Each knob has one job: **turn** + **short press**.

| Knob | Label | Turn | Press |
|------|-------|------|-------|
| **Rooms** | `ROOMS` | Scroll room **presets** | Apply highlighted preset |
| **Music** | `MUSIC` | Scroll favorites / playlists | Load & play selection |
| **Vol** | `VOL` | Volume up/down (also unmutes if muted) | **Mute / unmute** |
| **Transport** | `SKIP` | Previous / next track | Play / pause |

Shuffle / rare actions: later (e.g. double-tap Music) or `/ui` only — not long-press.

**Implementation notes**

- Debounced short press only; no hold state machine for v1 product UX.
- Mute: press Vol → muted; turn Vol or press again → unmute (turn applies Δ from current level after unmute).
- Transport skip may no-op on radio/line-in; show a brief toast.

---

## Room presets: what "select rooms" means

- Playback targets a **Sonos group**; presets are named groups of **rooms/players** (e.g. "All", "Bedroom", "Kitchen + Living + Bath").
- Same idea as playlists: **editable in config** and `/ui`.
- **Not** on-device multi-select checkbox grouping for v1 — presets only.

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

- `rooms[]` → stable Sonos identifiers (names or player IDs) once discovery is wired.
- **Persist** `active_room_preset` across reboots unless you explicitly want session-only.

---

## Screen UX: chrome + one active menu + idle splash

**Philosophy:** while interacting, show chrome + **one** list (last dial touched). After idle, fade to a **now-playing splash** (big album art). No nested settings menus on-device.

### Active UI (any knob recently touched)

**Always-on chrome (header)**

- Active room preset name  
- Play state + volume % + muted indicator  
- Now playing: title · artist (marquee if needed)

**Soft label row (footer)** — fixed, aligned to the four physical knobs:

`ROOMS · MUSIC · VOL · SKIP`

Highlight the label for the **active** dial (last touched). Optional tiny press hint under each label (`apply` / `play` / `mute` / `▶❚❚`).

**Middle (interactive)**

| Last dial | Middle content |
|-----------|----------------|
| Rooms | Preset list; highlight moves with turn |
| Music | Favorites list |
| Vol | Optional large volume focus (or chrome-only) |
| Transport | No list — flash prev/next / update track in chrome |

**Rough layout (active)**

```
┌─────────── Downstairs · ▶ · 42% ───────────┐
│  GET HAPPY! · Some Artist                  │
├────────────────────────────────────────────┤
│  > Downstairs                              │  ← only while Rooms active
│    Bedroom                                 │
│    All                                     │
├─────────┬─────────┬─────────┬──────────────┤
│ ROOMS   │ MUSIC   │  VOL    │    SKIP      │
│ active  │         │         │              │
└─────────┴─────────┴─────────┴──────────────┘
      (o)       (o)       (o)        (o)
```

### Idle timeouts (two steps)

| After… | What happens |
|--------|----------------|
| **~3–5 s** no knob activity | Collapse middle list → chrome-only (labels still visible) |
| **~30–60 s** no knob activity (tune; default **45 s**) | Fade to **now-playing splash** |

Any turn or press on any knob → cancel splash, restore active UI (chrome + that dial’s menu if it was a list dial).

### Now-playing splash (idle)

Full-bleed (or near full-bleed) **album art**, UI chrome derived from art colors when we can (palette from cover; fallback dark).

Show on the splash (sparse — not a second control deck):

- Large title + artist  
- Small room chip (e.g. `Downstairs` / `Kitchen + 1`)  
- Subtle play/pause + volume or mute glyph (glanceable; knobs still do the work)  
- Optional thin progress if metadata is cheap; skip if not

Do **not** show soft knob labels or lists on the splash — it’s ambient. First knob touch brings labels back.

Art source: Sonos playback metadata / album art URL (cache locally; placeholder gradient if missing). Night: keep splash but respect low backlight — art shouldn’t blow out the room.

**4" vs 5":** both fine for chrome + one list + four labels; 5" is nicer for splash art. Do **not** show Rooms + Music lists at once unless the panel is large enough that it stays readable (optional later).

---

## Configuration UX (Mac-first, Pi later)

Extend `/ui` (and JSON API) the same way as playlists:

- Discover rooms, edit `room_presets`, set `active_room_preset`
- Endpoints like `GET /rooms`, `GET/PUT /room_presets`, `POST /active_room_preset`, plus `/reload`

---

## Future upgrades (not required for core)

- Recessed bezel / subtle face lighting (separate from display backlight).
- On-device shuffle gesture; alarm / wake integration with house lighting.
- **Jellyfin → Sonos** — separate ecosystem; treat as future work.

---

## Summary

| Topic | Decision |
|-------|----------|
| Hardware core | **Target:** Pi Zero 2 WH + **no-touch HDMI** 5" (#1928) or Waveshare 4–5" HDMI (touch unused) + **4×** Bourns on Adafruit #5752. **Interim:** Zero W + Sharp #3502 + 2 encoders |
| Knobs | **ROOMS / MUSIC / VOL / SKIP** — turn + short press only; **no long-press** |
| Vol press | **Mute / unmute**; turn adjusts level (unmutes if needed) |
| Transport | Turn = prev/next; press = play/pause |
| Rooms | **Presets** only; scroll + press apply |
| Screen | HDMI color; **chrome + one menu** while active; **~45 s idle → album-art now-playing splash**; any knob wakes UI |
| Editing lists | **`/ui` on LAN** |
