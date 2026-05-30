import os
import sys
import threading
import time
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

    from sharp_hw import open_display, pattern_blank, pattern_happy_face, show_image

_SIM_HISTORY_MAX = 30

TEXT_PAD_LEFT = 4
TEXT_PAD_RIGHT = 2
MARQUEE_DELAY_S = 1.0
MARQUEE_SCROLL_PX_S = 28
MARQUEE_PAUSE_END_S = 1.2
MARQUEE_PAUSE_START_S = 0.8


def _text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _fit_text(draw, text, font, max_width):
    if _text_width(draw, text, font) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_width(draw, text[:mid] + ell, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + ell) if lo else ell


class Display:
    def __init__(self):
        self._view = "list"
        self._mq_key = None
        self._mq_overflow = 0
        self._mq_offset = 0.0
        self._mq_dwell_start = None
        self._mq_phase = "idle"
        self._mq_phase_start = None
        if SIMULATE:
            self._sim_history: deque[str] = deque(maxlen=_SIM_HISTORY_MAX)
            self._sim_lock = threading.Lock()
            print("[display] Simulated display ready")
        else:
            self._sim_history = None
            self._sim_lock = None
            self._disp, self._invert = open_display()
            self._list_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14
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

    def _list_text_width(self):
        return DISPLAY_WIDTH - TEXT_PAD_LEFT - TEXT_PAD_RIGHT

    def _reset_marquee(self, selected_index, name):
        key = (selected_index, name)
        if key == self._mq_key:
            return
        self._mq_key = key
        self._mq_offset = 0.0
        self._mq_dwell_start = time.monotonic()
        self._mq_phase = "idle"
        self._mq_phase_start = None
        if SIMULATE:
            self._mq_overflow = 0
            return
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        text_w = _text_width(probe, name, self._list_font)
        avail = self._list_text_width()
        self._mq_overflow = max(0, text_w - avail)

    def advance_marquee(self, selected_index, items):
        """Advance selected-item marquee; return True if the list should repaint."""
        if SIMULATE or self._view != "list" or not items:
            return False
        name = items[selected_index]["name"]
        self._reset_marquee(selected_index, name)
        if self._mq_overflow <= 0:
            return False

        now = time.monotonic()
        if now - self._mq_dwell_start < MARQUEE_DELAY_S:
            return False

        if self._mq_phase == "idle":
            self._mq_phase = "scroll"
            self._mq_phase_start = now

        elapsed = now - self._mq_phase_start
        if self._mq_phase == "scroll":
            duration = self._mq_overflow / MARQUEE_SCROLL_PX_S
            if elapsed >= duration:
                self._mq_offset = float(self._mq_overflow)
                self._mq_phase = "pause_end"
                self._mq_phase_start = now
                return True
            new_offset = elapsed * MARQUEE_SCROLL_PX_S
            if abs(new_offset - self._mq_offset) < 0.5:
                return False
            self._mq_offset = new_offset
            return True

        if self._mq_phase == "pause_end":
            if elapsed < MARQUEE_PAUSE_END_S:
                return False
            self._mq_offset = 0.0
            self._mq_phase = "pause_start"
            self._mq_phase_start = now
            return True

        if self._mq_phase == "pause_start":
            if elapsed < MARQUEE_PAUSE_START_S:
                return False
            self._mq_phase = "scroll"
            self._mq_phase_start = now
            return False

        return False

    def render_list(self, items, selected_index, header="Playlists"):
        self._view = "list"
        if items:
            self._reset_marquee(selected_index, items[selected_index]["name"])
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
        small = self._list_font
        text_w = self._list_text_width()

        draw.text((TEXT_PAD_LEFT, 2), header, font=font, fill=1)
        draw.line([(0, 14), (w - 1, 14)], fill=1, width=1)

        line_h = 18
        max_visible = max(1, (h - 18) // line_h)
        start = max(0, min(selected_index - max_visible // 2, len(items) - max_visible))
        visible = items[start : start + max_visible]

        for i, item in enumerate(visible):
            idx = start + i
            y = 18 + i * line_h
            name = item["name"]
            if idx == selected_index:
                draw.rectangle([(0, y - 1), (w - 1, y + line_h - 2)], fill=1)
                offset = int(self._mq_offset) if self._mq_overflow > 0 else 0
                strip = Image.new("1", (text_w, line_h), 1)
                strip_draw = ImageDraw.Draw(strip)
                strip_draw.text((-offset, 0), name, font=small, fill=0)
                img.paste(strip, (TEXT_PAD_LEFT, y))
            else:
                draw.text(
                    (TEXT_PAD_LEFT, y),
                    _fit_text(draw, name, small, text_w),
                    font=small,
                    fill=1,
                )

        show_image(self._disp, img, invert=self._invert)
        if sys.stdout.isatty() and items:
            print(f"[display] ▶ {items[selected_index]['name']}", flush=True)

    def render_volume_adjust(self, delta_percent):
        """Live volume feedback while turning the volume encoder."""
        self._view = "volume"
        label = f"{delta_percent:+d}%"
        if SIMULATE:
            self.sim_log(f"vol {label}")
            return

        w, h = DISPLAY_WIDTH, DISPLAY_HEIGHT
        img = Image.new("1", (w, h), 0)
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)

        draw.text((4, 2), "Volume", font=font, fill=1)
        draw.line([(0, 14), (w - 1, 14)], fill=1, width=1)
        draw.text((4, 40), label, font=big, fill=1)
        show_image(self._disp, img, invert=self._invert)
        if sys.stdout.isatty():
            print(f"[display] vol {label}", flush=True)

    def clear(self):
        """Blank the panel (Sharp memory displays hold the last frame until updated)."""
        if SIMULATE:
            return
        show_image(
            self._disp,
            pattern_blank(DISPLAY_WIDTH, DISPLAY_HEIGHT),
            invert=self._invert,
        )

    def show_goodbye(self):
        """Happy face on exit — Sharp panels hold the last frame until updated."""
        if SIMULATE:
            return
        show_image(
            self._disp,
            pattern_happy_face(DISPLAY_WIDTH, DISPLAY_HEIGHT),
            invert=self._invert,
        )
