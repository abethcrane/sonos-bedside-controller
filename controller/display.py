import os
import threading
from collections import deque

# Mac / dev: no SPI display. Pi with Sharp panel: /dev/spidev0.0 exists.
SIMULATE = not os.path.exists("/dev/spidev0.0")

SIM_HELP = """  Controls
  j = down   k = up   enter = select   space = play/pause
  + = vol+   - = vol-   q or ^C = quit"""

if not SIMULATE:
    import board, busio, adafruit_sharpmemorydisplay
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
            self.disp = adafruit_sharpmemorydisplay.SharpMemoryDisplay(
                spi, board.D6, 400, 240
            )

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
        img = Image.new("1", (240, 400), 1)  # portrait: swap w/h
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)

        draw.text((8, 8), header, font=font, fill=0)
        draw.line([(0, 32), (240, 32)], fill=0, width=1)

        for i, item in enumerate(items):
            y = 40 + i * 24
            if i == selected_index:
                draw.rectangle([(0, y-2), (240, y+20)], fill=0)
                draw.text((8, y), item["name"], font=small, fill=1)
            else:
                draw.text((8, y), item["name"], font=small, fill=0)

        self.disp.image(img.rotate(90, expand=True))
        self.disp.show()