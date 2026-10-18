#!/usr/bin/env python3
"""
Hardware bring-up for Adafruit I2C Quad Rotary Encoder Breakout (#5752).

Uses the same seesaw I2C reads as encoder.py / main.py.

Decode mode (default): one line per detent.
Raw mode (--raw): position counter + button state every change.

Seesaw switch pins (product #5752):
  slot 0 → 12,  slot 1 → 14,  slot 2 → 17,  slot 3 → 9
"""

import argparse
import os
import signal
import sys
import time

# Interim role map (matches main.py). Populate all four Bourns on #5752.
ENCODERS = [
    {"name": "MUSIC (enc 0)", "slot": 0, "sw_pin": 12},
    {"name": "ROOMS (enc 1)", "slot": 1, "sw_pin": 14},
    {"name": "VOL   (enc 2)", "slot": 2, "sw_pin": 17},
    {"name": "SKIP  (enc 3)", "slot": 3, "sw_pin": 9},
]


def main():
    parser = argparse.ArgumentParser(description="I2C rotary encoder probe")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="show position counter + button state on every change",
    )
    args = parser.parse_args()

    if not os.path.exists("/dev/i2c-1"):
        sys.exit("Run on the Pi — /dev/i2c-1 not found")

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
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    if args.raw:
        print("Raw mode — position + button on every change. Ctrl+C to stop.\n")
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
    else:
        print("Decode mode — one line per detent. Ctrl+C for summary.\n")
        try:
            while True:
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
                        print(
                            f"[{p['name']}] delta={delta:+d}  "
                            f"(+{p['plus']}/-{p['minus']})",
                            flush=True,
                        )
                    pressed = not p["sw"].value
                    was_pressed = not p["last_sw"]
                    if pressed and not was_pressed:
                        p["presses"] += 1
                        print(f"[{p['name']}] PRESS ({p['presses']})", flush=True)
                    p["last_sw"] = p["sw"].value
                time.sleep(0.005)
        except KeyboardInterrupt:
            print()
            for p in probes:
                print(
                    f"{p['name']}: +{p['plus']}  -{p['minus']}  "
                    f"presses={p['presses']}"
                )
            print("Bye.")


if __name__ == "__main__":
    main()
