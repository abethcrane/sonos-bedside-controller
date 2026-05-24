#!/usr/bin/env python3
"""Cycle test patterns on the Sharp display. Run on the Pi with wiring connected."""

import os
import sys
import time

from sharp_hw import (
    SharpDisplay,
    pattern_blank,
    pattern_border,
    pattern_fill,
    pattern_stripes,
    pattern_text,
)

PATTERNS = [
    ("blank (white paper)", pattern_blank),
    ("solid black fill", pattern_fill),
    ("border box", pattern_border),
    ("horizontal stripes", pattern_stripes),
    ("SONOS text", lambda w, h: pattern_text(w, h, "SONOS")),
]


def main():
    width = int(os.environ.get("DISPLAY_WIDTH", "250"))
    height = int(os.environ.get("DISPLAY_HEIGHT", "122"))
    hz = int(os.environ.get("DISPLAY_SPI_HZ", "2000000"))
    cs_high = os.environ.get("DISPLAY_CS_ACTIVE_HIGH", "1").lower() not in ("0", "false", "no")
    invert = os.environ.get("DISPLAY_INVERT", "").lower() in ("1", "true", "yes")

    print(f"Sharp test — {width}×{height} @ {hz}Hz  CS_active_high={cs_high}  invert={invert}")
    print("Ctrl+C to stop. Patterns cycle every 3s.\n")
    print("If still static, try:")
    print("  DISPLAY_WIDTH=122 DISPLAY_HEIGHT=250 python display_test.py")
    print("  DISPLAY_WIDTH=144 DISPLAY_HEIGHT=168 python display_test.py")
    print("  DISPLAY_SPI_HZ=500000 python display_test.py")
    print("  DISPLAY_CS_ACTIVE_HIGH=0 python display_test.py")
    print("  DISPLAY_INVERT=1 python display_test.py\n")

    disp = SharpDisplay(width, height, baudrate=hz, cs_active_high=cs_high)
    i = 0
    try:
        while True:
            name, fn = PATTERNS[i % len(PATTERNS)]
            print(f"→ {name}", flush=True)
            img = fn(width, height)
            disp.blit(img, invert=invert)
            disp.show()
            time.sleep(3)
            i += 1
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == "__main__":
    if not os.path.exists("/dev/spidev0.0"):
        sys.exit("SPI not enabled — run: sudo raspi-config → Interface Options → SPI")
    main()
