import os
import threading
from collections import deque

# Mac / dev: no SPI display. Pi with Sharp panel: /dev/spidev0.0 exists.
# Set SIMULATE_DISPLAY=1 to force terminal mode even if SPI is enabled.
SIMULATE = (
    not os.path.exists("/dev/spidev0.0")
    or os.environ.get("SIMULATE_DISPLAY", "").lower() in ("1", "true", "yes")
)

# Panel size — override via env for 2.7" #4694: DISPLAY_WIDTH=400 DISPLAY_HEIGHT=240
DISPLAY_WIDTH = int(os.environ.get("DISPLAY_WIDTH", "250"))
DISPLAY_HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "122"))

SIM_HELP = """  Controls
  j = down   k = up   enter = select   space = play/pause
  + = vol+   - = vol-   q or ^C = quit"""

if not SIMULATE:
    import board, busio, digitalio, adafruit_sharpmemorydisplay
    from PIL import Image, ImageDraw, ImageFont

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
            spi = busio.SPI(board.SCK, MOSI=board.MOSI)
            cs = digitalio.DigitalInOut(board.D8)
            self.disp = adafruit_sharpmemorydisplay.SharpMemoryDisplay(
                spi, cs, DISPLAY_WIDTH, DISPLAY_HEIGHT
            )
            print(f"[display] Sharp {DISPLAY_WIDTH}×{DISPLAY_HEIGHT} ready")

    def sim_log(self, line: str):
        """Append one line to the Mac simulator log (no-op on hardware)."""
        if not SIMULATE:
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

        # Real display rendering on Pi
        img = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)

        draw.text((4, 2), header, font=font, fill=0)
        draw.line([(0, 16), (DISPLAY_WIDTH, 16)], fill=0, width=1)

        line_h = 16
        max_visible = (DISPLAY_HEIGHT - 20) // line_h
        start = max(0, min(selected_index - max_visible // 2, len(items) - max_visible))
        visible = items[start : start + max_visible]

        for i, item in enumerate(visible):
            idx = start + i
            y = 20 + i * line_h
            name = item["name"]
            if len(name) > 28:
                name = name[:27] + "…"
            if idx == selected_index:
                draw.rectangle([(0, y - 1), (DISPLAY_WIDTH, y + line_h - 2)], fill=0)
                draw.text((4, y), name, font=small, fill=1)
            else:
                draw.text((4, y), name, font=small, fill=0)

        self.disp.image(img)
        self.disp.show()
