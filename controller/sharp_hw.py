"""Sharp memory display helpers — uses Adafruit driver (same path as display_probe)."""

import os

import adafruit_sharpmemorydisplay
import board
import busio
import digitalio
from PIL import Image, ImageDraw, ImageFont, ImageOps


def get_cs_pin():
    """Adafruit #3502 uses GPIO 6 for CS on Pi — not GPIO 8 (hardware CE0)."""
    n = int(os.environ.get("DISPLAY_CS_PIN", "6"))
    return getattr(board, f"D{n}")


def open_display():
    width = int(os.environ.get("DISPLAY_WIDTH", "144"))
    height = int(os.environ.get("DISPLAY_HEIGHT", "168"))
    invert = os.environ.get("DISPLAY_INVERT", "").lower() in ("1", "true", "yes")
    cs = digitalio.DigitalInOut(get_cs_pin())
    spi = busio.SPI(board.SCK, MOSI=board.MOSI)
    disp = adafruit_sharpmemorydisplay.SharpMemoryDisplay(spi, cs, width, height)
    return disp, invert


def show_image(disp, img, *, invert=False):
    """Push a PIL image to the panel (mode 1: 0=white paper, 1=ink)."""
    try:
        dither = Image.Dither.NONE
    except AttributeError:
        dither = Image.NONE
    img = img.convert("1", dither=dither)
    if invert:
        img = ImageOps.invert(img.convert("L")).convert("1")
    disp.image(img)
    disp.show()


def pattern_blank(w, h):
    return Image.new("1", (w, h), 0)


def pattern_fill(w, h):
    return Image.new("1", (w, h), 1)


def pattern_border(w, h, pad=2):
    img = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(pad, pad), (w - 1 - pad, h - 1 - pad)], outline=1, width=pad)
    return img


def pattern_stripes(w, h):
    img = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for y in range(0, h, 8):
        draw.rectangle([(0, y), (w - 1, y + 3)], fill=1)
    return img


def pattern_text(w, h, text="SONOS"):
    img = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    draw.text((4, h // 2 - 10), text, font=font, fill=1)
    return img
