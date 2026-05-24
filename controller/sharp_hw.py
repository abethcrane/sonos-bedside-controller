"""Low-level Sharp memory display driver for Raspberry Pi (Blinka + SPI)."""

import os

import adafruit_sharpmemorydisplay
import board
import busio
import digitalio
from adafruit_bus_device.spi_device import SPIDevice
from PIL import Image, ImageDraw, ImageFont

_SHARPMEM_BIT_WRITECMD = 0x80
_SHARPMEM_BIT_VCOM = 0x40
_reverse_bit = adafruit_sharpmemorydisplay.reverse_bit


def pack_pil(img, width, height, *, invert=False):
    """Pack a mode-1 PIL image into row-aligned Sharp SPI bytes."""
    line_len = (width + 7) // 8
    buf = bytearray(line_len * height)
    pixels = img.load()
    for y in range(height):
        row = y * line_len
        for x in range(width):
            on = bool(pixels[x, y])
            if invert:
                on = not on
            if on:
                buf[row + x // 8] |= 1 << (7 - (x & 7))
    return buf


class SharpDisplay:
    def __init__(self, width, height, *, cs_pin=board.D8, baudrate=2_000_000, cs_active_high=True):
        self.width = width
        self.height = height
        self.cs_active_high = cs_active_high
        spi = busio.SPI(board.SCK, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(cs_pin)
        cs.switch_to_output(value=True)
        self.spi_device = SPIDevice(
            spi, cs, cs_active_value=cs_active_high, baudrate=baudrate
        )
        self._cmd = bytearray(1)
        line_len = (width + 7) // 8
        self.buffer = bytearray(line_len * height)
        self._vcom = True

    def blit(self, img, *, invert=False):
        try:
            dither = Image.Dither.NONE
        except AttributeError:
            dither = Image.NONE
        img = img.convert("1", dither=dither)
        self.buffer = pack_pil(img, self.width, self.height, invert=invert)

    def show(self):
        line_len = (self.width + 7) // 8
        with self.spi_device as spi:
            out = bytearray()
            self._cmd[0] = _SHARPMEM_BIT_WRITECMD | (_SHARPMEM_BIT_VCOM if self._vcom else 0)
            self._vcom = not self._vcom
            out.extend(self._cmd)

            offset = 0
            for line in range(self.height):
                self._cmd[0] = _reverse_bit(line + 1)
                out.extend(self._cmd)
                out.extend(self.buffer[offset : offset + line_len])
                offset += line_len

            self._cmd[0] = 0
            out.extend(self._cmd)
            out.extend(self._cmd)
            spi.write(out)


def open_display():
    width = int(os.environ.get("DISPLAY_WIDTH", "144"))
    height = int(os.environ.get("DISPLAY_HEIGHT", "168"))
    hz = int(os.environ.get("DISPLAY_SPI_HZ", "2000000"))
    cs_high = os.environ.get("DISPLAY_CS_ACTIVE_HIGH", "1").lower() not in ("0", "false", "no")
    invert = os.environ.get("DISPLAY_INVERT", "").lower() in ("1", "true", "yes")
    return SharpDisplay(width, height, baudrate=hz, cs_active_high=cs_high), invert


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
