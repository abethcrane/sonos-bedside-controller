#!/usr/bin/env python3
"""Cycle test patterns on the Sharp display. Run on the Pi with wiring connected."""

import os
import sys
import time

from sharp_hw import (
    display_invert,
    open_display,
    pattern_blank,
    pattern_border,
    pattern_fill,
    pattern_stripes,
    pattern_text,
    show_image,
)

PATTERNS = [
    ("blank (white paper)", pattern_blank),
    ("solid black fill", pattern_fill),
    ("border box", pattern_border),
    ("horizontal stripes", pattern_stripes),
    ("SONOS text", lambda w, h: pattern_text(w, h, "SONOS")),
]


def main():
    width = int(os.environ.get("DISPLAY_WIDTH", "144"))
    height = int(os.environ.get("DISPLAY_HEIGHT", "168"))
    invert = display_invert()

    print(f"Sharp test — {width}×{height}  invert={invert}")
    print("Ctrl+C to stop. Patterns cycle every 3s.\n")
    print("If colors look wrong, try:")
    print("  DISPLAY_INVERT=0 python display_test.py\n")

    disp, _ = open_display()
    i = 0
    try:
        while True:
            name, fn = PATTERNS[i % len(PATTERNS)]
            print(f"→ {name}", flush=True)
            img = fn(width, height)
            show_image(disp, img, invert=invert)
            time.sleep(3)
            i += 1
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == "__main__":
    if not os.path.exists("/dev/spidev0.0"):
        sys.exit("SPI not enabled — run: sudo raspi-config → Interface Options → SPI")
    main()
