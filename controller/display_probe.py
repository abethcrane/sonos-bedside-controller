#!/usr/bin/env python3
"""
Hardware bring-up for Adafruit #3502 (144×168 Sharp display).

If the panel shows frozen static and never changes, CS is almost always the issue.
Adafruit wires CS to GPIO 6 (physical pin 31) — NOT GPIO 8 (pin 24).
"""

import os
import sys
import time

import board
import busio
import digitalio
import adafruit_sharpmemorydisplay
from PIL import Image, ImageDraw

WIDTH, HEIGHT = 144, 168


def try_adafruit(cs_gpio: int):
    print(f"\n=== Adafruit driver, CS=GPIO {cs_gpio} (pin { {'6': 31, '8': 24}.get(str(cs_gpio), '?')} ) ===")
    cs = digitalio.DigitalInOut(getattr(board, f"D{cs_gpio}"))
    spi = busio.SPI(board.SCK, MOSI=board.MOSI)
    disp = adafruit_sharpmemorydisplay.SharpMemoryDisplay(spi, cs, WIDTH, HEIGHT)

    for name, fill in [("WHITE paper", 0), ("BLACK fill", 1), ("SPLIT half", None)]:
        print(f"  → {name} (2s)", flush=True)
        img = Image.new("1", (WIDTH, HEIGHT), 0)
        if fill == 1:
            ImageDraw.Draw(img).rectangle([(0, 0), (WIDTH - 1, HEIGHT - 1)], fill=1)
        elif fill is None:
            d = ImageDraw.Draw(img)
            d.rectangle([(0, 0), (WIDTH // 2 - 1, HEIGHT - 1)], fill=1)
        disp.image(img)
        disp.show()
        time.sleep(2)


def main():
    if not os.path.exists("/dev/spidev0.0"):
        sys.exit("SPI not enabled — sudo raspi-config → Interface Options → SPI")

    print("Adafruit #3502 probe — 144×168")
    print()
    print("WIRING CHECK (power off while plugging):")
    print("  Red   → VIN (pin 1)  AND  DISP (pin 17)   ← both must be 3.3V")
    print("  Blue  → GND (pin 25) AND  EMD (pin 30)    ← both must be GND")
    print("  Orange→ CLK  (pin 23 / GPIO 11)")
    print("  Yellow→ DI   (pin 19 / GPIO 10)")
    print("  Green → CS   (pin 31 / GPIO 6)  ← Adafruit default, try this first")
    print()
    print("Unplug encoder 2 DT (grey) from pin 31 if it's there — CS shares GPIO 6.")
    print("Check the flat flex cable is seated in the breakout FPC connector.")
    print()

    cs = os.environ.get("DISPLAY_CS_PIN")
    if cs:
        try_adafruit(int(cs))
    else:
        for gpio in (6, 8):
            try:
                try_adafruit(gpio)
            except Exception as e:
                print(f"  ERROR: {e}")

    print("\nDone. Did the panel change at all?")


if __name__ == "__main__":
    main()
