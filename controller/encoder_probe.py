#!/usr/bin/env python3
"""
Hardware bring-up for Adafruit I2C Quad Rotary Encoder Breakout (#5752).

Uses the same seesaw I2C reads as encoder.py / main.py.

Modes:
  (default) checklist — live pass/fail until each slot turns + presses
  --decode            — one line per detent (old default)
  --raw               — position + button state on every change

Seesaw switch pins (product #5752):
  slot 0 → 12,  slot 1 → 14,  slot 2 → 17,  slot 3 → 9

On the Pi (after git pull):
  sudo systemctl stop sonos    # free the I2C bus
  cd ~/sonos-bedside-controller/controller
  python encoder_probe.py
"""

import argparse
import os
import signal
import sys
import time

# Interim role map (matches main.py). Populate all four Bourns on #5752.
ENCODERS = [
    {"name": "MUSIC", "slot": 0, "sw_pin": 12},
    {"name": "ROOMS", "slot": 1, "sw_pin": 14},
    {"name": "VOL",   "slot": 2, "sw_pin": 17},
    {"name": "SKIP",  "slot": 3, "sw_pin": 9},
]


def _ok(flag):
    return "✓" if flag else "·"


def _print_status(probes, clear=True):
    if clear:
        # ANSI clear + home — works fine over SSH
        sys.stdout.write("\033[2J\033[H")
    print("Encoder checklist — turn each knob both ways, press each button")
    print("Ctrl+C when done (or when all four pass)\n")
    print(f"  {'':6}  {'slot':>4}  {'sw':>3}  {'CW':>3}  {'CCW':>3}  {'press':>5}  status")
    print("  " + "─" * 48)
    all_pass = True
    for p in probes:
        cw = p["plus"] > 0
        ccw = p["minus"] > 0
        press = p["presses"] > 0
        passed = cw and ccw and press
        all_pass = all_pass and passed
        status = "PASS" if passed else "…"
        print(
            f"  {p['name']:6}  {p['slot']:>4}  {p['sw_pin']:>3}  "
            f"{_ok(cw):>3}  {_ok(ccw):>3}  {_ok(press):>5}  {status}"
            f"   (+{p['plus']}/-{p['minus']}  presses={p['presses']})"
        )
    print()
    if all_pass:
        print("  ★ all four encoders OK")
    sys.stdout.flush()
    return all_pass


def _open_probes():
    from board import SCL, SDA
    import busio
    from adafruit_seesaw import seesaw, rotaryio, digitalio
    from digitalio import Direction, Pull

    i2c = busio.I2C(SCL, SDA)
    ss = seesaw.Seesaw(i2c, addr=0x49)
    print("seesaw connected (addr=0x49)\n")

    probes = []
    for enc in ENCODERS:
        e = rotaryio.IncrementalEncoder(ss, enc["slot"])
        sw = digitalio.DigitalIO(ss, enc["sw_pin"])
        sw.direction = Direction.INPUT
        sw.pull = Pull.UP
        probes.append({
            "name": enc["name"],
            "slot": enc["slot"],
            "sw_pin": enc["sw_pin"],
            "enc": e,
            "sw": sw,
            "last_pos": e.position,
            "last_sw": sw.value,
            "plus": 0,
            "minus": 0,
            "presses": 0,
        })
        print(f"  {enc['name']}: slot {enc['slot']}, switch pin {enc['sw_pin']}")
    print()
    return probes


def _poll_once(probes, on_event=None):
    """Update probe counters. Returns True if anything changed."""
    changed = False
    for p in probes:
        pos = p["enc"].position
        if pos != p["last_pos"]:
            delta = pos - p["last_pos"]
            if delta > 0x7FFFFFFF:
                delta -= 0x100000000
            elif delta < -0x80000000:
                delta += 0x100000000
            p["last_pos"] = pos
            if delta > 0:
                p["plus"] += delta
            else:
                p["minus"] += abs(delta)
            changed = True
            if on_event:
                on_event(p, "turn", delta)
        pressed = not p["sw"].value
        was_pressed = not p["last_sw"]
        if pressed and not was_pressed:
            p["presses"] += 1
            changed = True
            if on_event:
                on_event(p, "press", None)
        p["last_sw"] = p["sw"].value
    return changed


def run_checklist(probes):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _print_status(probes)
    try:
        while True:
            if _poll_once(probes):
                if _print_status(probes):
                    # hold the PASS screen briefly, then exit clean
                    time.sleep(0.4)
                    print("\nBye — all slots confirmed.")
                    return 0
            time.sleep(0.005)
    except KeyboardInterrupt:
        print()
        _print_status(probes, clear=False)
        failed = [
            p["name"]
            for p in probes
            if not (p["plus"] > 0 and p["minus"] > 0 and p["presses"] > 0)
        ]
        if failed:
            print(f"\nIncomplete: {', '.join(failed)}")
            return 1
        print("\nBye — all slots confirmed.")
        return 0


def run_decode(probes):
    print("Decode mode — one line per detent. Ctrl+C for summary.\n")
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        while True:
            def on_event(p, kind, delta):
                if kind == "turn":
                    print(
                        f"[{p['name']}] delta={delta:+d}  "
                        f"(+{p['plus']}/-{p['minus']})",
                        flush=True,
                    )
                else:
                    print(f"[{p['name']}] PRESS ({p['presses']})", flush=True)

            _poll_once(probes, on_event=on_event)
            time.sleep(0.005)
    except KeyboardInterrupt:
        print()
        for p in probes:
            print(
                f"{p['name']}: +{p['plus']}  -{p['minus']}  "
                f"presses={p['presses']}"
            )
        print("Bye.")
        return 0


def run_raw(probes):
    print("Raw mode — position + button on every change. Ctrl+C to stop.\n")
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    t0 = time.monotonic()
    try:
        while True:
            ts = f"{time.monotonic() - t0:8.3f}"
            for p in probes:
                pos = p["enc"].position
                if pos != p["last_pos"]:
                    delta = pos - p["last_pos"]
                    if delta > 0x7FFFFFFF:
                        delta -= 0x100000000
                    elif delta < -0x80000000:
                        delta += 0x100000000
                    print(f"  {ts}  [{p['name']}] pos={pos}  delta={delta}")
                    p["last_pos"] = pos
                sw_val = p["sw"].value
                if sw_val != p["last_sw"]:
                    state = "UP" if sw_val else "DOWN"
                    print(f"  {ts}  [{p['name']}] button {state}")
                    p["last_sw"] = sw_val
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\nBye.")
        return 0


def main():
    parser = argparse.ArgumentParser(description="I2C rotary encoder probe")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--decode",
        action="store_true",
        help="one line per detent (no checklist board)",
    )
    mode.add_argument(
        "--raw",
        action="store_true",
        help="show position counter + button state on every change",
    )
    args = parser.parse_args()

    if not os.path.exists("/dev/i2c-1"):
        sys.exit("Run on the Pi — /dev/i2c-1 not found")

    probes = _open_probes()
    if args.raw:
        raise SystemExit(run_raw(probes))
    if args.decode:
        raise SystemExit(run_decode(probes))
    raise SystemExit(run_checklist(probes))


if __name__ == "__main__":
    main()
