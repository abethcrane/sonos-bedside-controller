#!/usr/bin/env python3
"""
Hardware bring-up for Bourns PEC11R rotary encoders.

Decode mode (default): prints +1 / -1 per detent with CLK/DT at decode time.
Raw mode (--raw): prints whenever CLK or DT changes — use to see if DT toggles at all.

If you only ever see one direction, DT is probably stuck, floating, or on the wrong pin.
"""

import argparse
import os
import signal
import sys
import time

if os.path.exists("/dev/gpiomem"):
    import pigpio

ENCODERS = [
    {
        "name": "playlist (enc 1)",
        "clk": 17,
        "dt": 27,
        "sw": 22,
        "clk_pin": 11,
        "dt_pin": 13,
    },
    {
        "name": "volume (enc 2)",
        "clk": 5,
        "dt": 26,
        "sw": 13,
        "clk_pin": 29,
        "dt_pin": 37,
    },
]


def wiring_banner():
    print("Rotary encoder probe")
    print()
    print("Turn each knob both ways.")
    print("Decode mode: expect +1 and -1 from each encoder.")
    print("If only one direction appears, check the grey DT wire for that encoder.")
    print()
    for enc in ENCODERS:
        print(
            f"  {enc['name']}: CLK white -> GPIO {enc['clk']} (pin {enc['clk_pin']}), "
            f"DT grey -> GPIO {enc['dt']} (pin {enc['dt_pin']})"
        )
    print()
    print("Requires pigpiod: sudo systemctl start pigpiod")
    print()


class EncoderProbe:
    def __init__(self, name, pi, clk, dt):
        self.name = name
        self.pi = pi
        self.clk = clk
        self.dt = dt
        self.plus = 0
        self.minus = 0

        for pin in (clk, dt):
            pi.set_mode(pin, pigpio.INPUT)
            pi.set_pull_up_down(pin, pigpio.PUD_UP)

        self._cb = pi.callback(clk, pigpio.RISING_EDGE, self._on_clk)

    def _on_clk(self, gpio, level, tick):
        clk = self.pi.read(self.clk)
        dt = self.pi.read(self.dt)
        if dt == 0:
            direction = +1
            self.plus += 1
        else:
            direction = -1
            self.minus += 1
        print(
            f"[{self.name}] dir={direction:+d}  CLK={clk} DT={dt}  "
            f"(+{self.plus}/-{self.minus})",
            flush=True,
        )

    def cancel(self):
        self._cb.cancel()

    def warn_if_one_way(self):
        if self.plus and not self.minus:
            print(f"  !! {self.name}: only +1 — DT may be stuck low")
        elif self.minus and not self.plus:
            print(f"  !! {self.name}: only -1 — DT may be stuck high / floating")


def run_decode(pi):
    print("Decode mode — Ctrl+C for summary.")
    print()

    probes = [
        EncoderProbe(enc["name"], pi, enc["clk"], enc["dt"]) for enc in ENCODERS
    ]

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        for probe in probes:
            print(
                f"{probe.name}: +{probe.plus}  -{probe.minus}  "
                f"(total {probe.plus + probe.minus})"
            )
            probe.warn_if_one_way()
        print("Bye.")
    finally:
        for probe in probes:
            probe.cancel()


def run_raw(pi):
    print("Raw pin watch — turn slowly. Both CLK and DT should toggle each encoder.")
    print("Ctrl+C to stop.")
    print()

    last = {}
    for enc in ENCODERS:
        for pin in (enc["clk"], enc["dt"]):
            pi.set_mode(pin, pigpio.INPUT)
            pi.set_pull_up_down(pin, pigpio.PUD_UP)
        last[enc["name"]] = (pi.read(enc["clk"]), pi.read(enc["dt"]))

    try:
        while True:
            for enc in ENCODERS:
                clk = pi.read(enc["clk"])
                dt = pi.read(enc["dt"])
                state = (clk, dt)
                if last[enc["name"]] != state:
                    last[enc["name"]] = state
                    print(f"[{enc['name']}] CLK={clk} DT={dt}", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nBye.")


def main():
    parser = argparse.ArgumentParser(description="Rotary encoder wiring probe")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="watch CLK/DT pin levels instead of decoded direction",
    )
    args = parser.parse_args()

    if not os.path.exists("/dev/gpiomem"):
        sys.exit("Run on the Pi — not Mac keyboard/sim mode")

    pi = pigpio.pi()
    if not pi.connected:
        sys.exit("pigpio daemon not running — try: sudo systemctl start pigpiod")

    wiring_banner()

    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    try:
        if args.raw:
            run_raw(pi)
        else:
            run_decode(pi)
    finally:
        pi.stop()


if __name__ == "__main__":
    main()
