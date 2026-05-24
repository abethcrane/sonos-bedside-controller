import os
import sys
import threading
from collections import deque

# Mac / dev: no SPI display. Pi with Sharp panel: /dev/spidev0.0 exists.
# Set SIMULATE_DISPLAY=1 to force terminal mode even if SPI is enabled.
SIMULATE = (
    not os.path.exists("/dev/spidev0.0")
    or os.environ.get("SIMULATE_DISPLAY", "").lower() in ("1", "true", "yes")
)

DISPLAY_WIDTH = int(os.environ.get("DISPLAY_WIDTH", "144"))
DISPLAY_HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "168"))

SIM_HELP = """  Controls
  j = down   k = up   enter = select   space = play/pause
  + = vol+   - = vol-   q or ^C = quit"""

if not SIMULATE:
    from PIL import Image, ImageDraw, ImageFont

    from sharp_hw import open_display, pattern_blank, show_image

_SIM_HISTORY_MAX = 30


class Display:
    def __init__(self):
        if SIMULATE:
            self._sim_history: deque[str] = deque(maxlen=_SIM_HISTORY_MAX)
            self._sim_lock = threading.Lock()
            print("[display] Simulated display ready")
        else:
            self._sim_history = None
            self._sim_lock = None
            self._disp, self._invert = open_display()
            print(f"[display] Sharp {DISPLAY_WIDTH}×{DISPLAY_HEIGHT} ready")

    def sim_log(self, line: str):
        """Log to simulator history; also echo to SSH terminal when attached."""
        if not SIMULATE:
            if sys.stdout.isatty():
                print(f"  {line}", flush=True)
            return
        with self._sim_lock:
            self._sim_history.append(line)

    def render_list(self, items, selected_index, header="Playlists"):
        if SIMULATE:
            os.system("clear")
            print(SIM_HELP)
            print()
            with self._sim_lock:
                hist = list(self._sim_history)
            if hist:
                print("  ── log ─────────────────────────────")
                for line in hist:
                    print(f"  {line}")
                print()
            print(f"  {header}")
            print("  " + "─" * 30)
            for i, item in enumerate(items):
                prefix = " ▶ " if i == selected_index else "   "
                print(f"{prefix}{item['name']}")
            return

        w, h = DISPLAY_WIDTH, DISPLAY_HEIGHT
        img = Image.new("1", (w, h), 0)
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)

        draw.text((4, 2), header, font=font, fill=1)
        draw.line([(0, 14), (w - 1, 14)], fill=1, width=1)

        line_h = 14
        max_visible = max(1, (h - 18) // line_h)
        start = max(0, min(selected_index - max_visible // 2, len(items) - max_visible))
        visible = items[start : start + max_visible]

        for i, item in enumerate(visible):
            idx = start + i
            y = 18 + i * line_h
            name = item["name"]
            if len(name) > 16:
                name = name[:15] + "…"
            if idx == selected_index:
                draw.rectangle([(0, y - 1), (w - 1, y + line_h - 2)], fill=1)
                draw.text((4, y), name, font=small, fill=0)
            else:
                draw.text((4, y), name, font=small, fill=1)

        show_image(self._disp, img, invert=self._invert)
        if sys.stdout.isatty() and items:
            print(f"[display] ▶ {items[selected_index]['name']}", flush=True)

    def clear(self):
        """Blank the panel (Sharp memory displays hold the last frame until updated)."""
        if SIMULATE:
            return
        show_image(
            self._disp,
            pattern_blank(DISPLAY_WIDTH, DISPLAY_HEIGHT),
            invert=self._invert,
        )
