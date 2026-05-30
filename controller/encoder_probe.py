#!/usr/bin/env python3
"""
Hardware bring-up for KY-040 rotary encoder modules (PEC11R on PCB).

Uses the same sequence_step() FSM as encoder.py / main.py — good counts here
but slow SPI in the app mean you must test production behavior separately.

Wiring: CLK, DT, SW, GND to Pi; leave module + unconnected. Do not tie + to GND.

Decode mode (default): one line per detent (~20 per full turn).
Raw mode (--raw): CLK/DT levels while turning — DT must toggle with CLK.
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
    print("Rotary encoder probe (KY-040: CLK, DT, SW, GND; leave + unconnected)")
    print()
    print("Turn each knob both ways.")
    print("Decode mode: expect one line per detent (~20 per full turn on KY-040).")
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


from encoder import (
    GPIO_GLITCH_FILTER_US,
    sequence_step,
)


class EncoderProbe:
    def __init__(self, name, pi, clk, dt):
        self.name = name
        self.pi = pi
        self.clk = clk
        self.dt = dt
        self.plus = 0
        self.minus = 0
        self._seq_state = (0, 0, 0)

        for pin in (clk, dt):
            pi.set_mode(pin, pigpio.INPUT)
            pi.set_pull_up_down(pin, pigpio.PUD_UP)
        pi.set_glitch_filter(clk, GPIO_GLITCH_FILTER_US)
        pi.set_glitch_filter(dt, GPIO_GLITCH_FILTER_US)

        self._cb_clk = pi.callback(clk, pigpio.EITHER_EDGE, self._on_quad)
        self._cb_dt = pi.callback(dt, pigpio.EITHER_EDGE, self._on_quad)

    def _read_pin_state(self):
        clk1, dt1 = self.pi.read(self.clk), self.pi.read(self.dt)
        clk2, dt2 = self.pi.read(self.clk), self.pi.read(self.dt)
        if clk1 != clk2 or dt1 != dt2:
            clk2, dt2 = self.pi.read(self.clk), self.pi.read(self.dt)
        return (clk2 << 1) | dt2

    def _on_quad(self, gpio, level, tick):
        pin_state = self._read_pin_state()
        self._seq_state, direction = sequence_step(
            self._seq_state, pin_state, tick
        )
        if not direction:
            return
        clk = pin_state >> 1
        dt = pin_state & 1
        if direction > 0:
            self.plus += 1
        else:
            self.minus += 1
        print(
            f"[{self.name}] dir={direction:+d}  CLK={clk} DT={dt}  "
            f"(+{self.plus}/-{self.minus})",
            flush=True,
        )

    def cancel(self):
        self._cb_clk.cancel()
        self._cb_dt.cancel()

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
