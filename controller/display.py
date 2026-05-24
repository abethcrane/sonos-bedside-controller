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

# Panel size — override via env for 2.7" #4694: DISPLAY_WIDTH=400 DISPLAY_HEIGHT=240
DISPLAY_WIDTH = int(os.environ.get("DISPLAY_WIDTH", "250"))
DISPLAY_HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "122"))

DISPLAY_SPI_HZ = int(os.environ.get("DISPLAY_SPI_HZ", "2000000"))

SIM_HELP = """  Controls
  j = down   k = up   enter = select   space = play/pause
  + = vol+   - = vol-   q or ^C = quit"""

if not SIMULATE:
    import adafruit_sharpmemorydisplay
    import board
    import busio
    import digitalio
    from adafruit_bus_device.spi_device import SPIDevice
    from PIL import Image, ImageDraw, ImageFont

    _SHARPMEM_BIT_WRITECMD = 0x80
    _SHARPMEM_BIT_VCOM = 0x40
    _reverse_bit = adafruit_sharpmemorydisplay.reverse_bit

    def _pack_sharp_buffer(pixels, width, height):
        """Row-aligned SPI buffer — required when width is not divisible by 8 (e.g. 250)."""
        line_len = (width + 7) // 8
        buf = bytearray(line_len * height)
        for y in range(height):
            row = y * line_len
            for x in range(width):
                if pixels[x, y]:
                    buf[row + x // 8] |= 1 << (7 - (x & 7))
        return buf

    class SharpMemoryDisplay:
        """Minimal Sharp driver — bypasses Adafruit framebuf packing bug at 250px width."""

        def __init__(self, spi, scs_pin, width, height, *, baudrate=2000000):
            scs_pin.switch_to_output(value=True)
            self.spi_device = SPIDevice(spi, scs_pin, cs_active_value=True, baudrate=baudrate)
            self._buf = bytearray(1)
            self.width = width
            self.height = height
            line_len = (width + 7) // 8
            self.buffer = bytearray(line_len * height)
            self._vcom = True

        def show(self) -> None:
            line_len = (self.width + 7) // 8
            with self.spi_device as spi:
                image_buffer = bytearray()
                self._buf[0] = _SHARPMEM_BIT_WRITECMD
                if self._vcom:
                    self._buf[0] |= _SHARPMEM_BIT_VCOM
                self._vcom = not self._vcom
                image_buffer.extend(self._buf)

                slice_from = 0
                for line in range(self.height):
                    self._buf[0] = _reverse_bit(line + 1)
                    image_buffer.extend(self._buf)
                    image_buffer.extend(self.buffer[slice_from : slice_from + line_len])
                    slice_from += line_len
                self._buf[0] = 0
                image_buffer.extend(self._buf)
                image_buffer.extend(self._buf)
                spi.write(image_buffer)

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
            self.disp = SharpMemoryDisplay(
                spi, cs, DISPLAY_WIDTH, DISPLAY_HEIGHT, baudrate=DISPLAY_SPI_HZ
            )
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

        # Sharp: 0 = light (paper), 1 = dark pixel
        w, h = DISPLAY_WIDTH, DISPLAY_HEIGHT
        img = Image.new("1", (w, h), 0)
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)

        draw.text((4, 2), header, font=font, fill=1)
        draw.line([(0, 16), (w - 1, 16)], fill=1, width=1)

        line_h = 16
        max_visible = max(1, (h - 20) // line_h)
        start = max(0, min(selected_index - max_visible // 2, len(items) - max_visible))
        visible = items[start : start + max_visible]

        for i, item in enumerate(visible):
            idx = start + i
            y = 20 + i * line_h
            name = item["name"]
            if len(name) > 28:
                name = name[:27] + "…"
            if idx == selected_index:
                draw.rectangle([(0, y - 1), (w - 1, y + line_h - 2)], fill=1)
                draw.text((4, y), name, font=small, fill=0)
            else:
                draw.text((4, y), name, font=small, fill=1)

        self.disp.buffer = _pack_sharp_buffer(img.load(), w, h)
        self.disp.show()
        if sys.stdout.isatty() and items:
            print(f"[display] ▶ {items[selected_index]['name']}", flush=True)
