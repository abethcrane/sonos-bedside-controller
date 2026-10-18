"""
Rotary encoder input via Adafruit I2C Quad Rotary Encoder Breakout (#5752).

The seesaw chip handles quadrature decoding in hardware — no GPIO timing
sensitivity, no missed edges during SPI redraws.

Button: polled.
  - short press → on_press (on release)
  - long press  → on_long_press (fires once while held; suppresses short press)

Pull-up enabled on the seesaw so the pin doesn't float on release.

Call poll() from the main loop (~5–20ms).
"""

import os
import time

SIMULATE = not os.path.exists("/dev/i2c-1")

if not SIMULATE:
    from board import SCL, SDA
    import busio
    from adafruit_seesaw import seesaw, rotaryio, digitalio
    from digitalio import Direction, Pull

SEESAW_ADDR = int(os.environ.get("SEESAW_ADDR", "0x49"), 0)
MIN_HOLD_S = float(os.environ.get("ENCODER_MIN_HOLD_S", "0.05"))
LONG_PRESS_S = float(os.environ.get("ENCODER_LONG_PRESS_S", "0.55"))
MIN_BETWEEN_PRESS_S = float(os.environ.get("ENCODER_MIN_BETWEEN_S", "0.30"))
STUCK_PRESS_S = 5.0  # re-apply pull-up if "pressed" this long (seesaw reset)

_seesaw_instance = None


def _get_seesaw():
    global _seesaw_instance
    if _seesaw_instance is None:
        i2c = busio.I2C(SCL, SDA)
        _seesaw_instance = seesaw.Seesaw(i2c, addr=SEESAW_ADDR)
        print(f"[encoder] seesaw connected (addr=0x{SEESAW_ADDR:02x})")
    return _seesaw_instance


class Encoder:
    def __init__(self, slot, sw_pin, on_rotate, on_press, on_long_press=None):
        self.on_rotate = on_rotate
        self.on_press = on_press
        self.on_long_press = on_long_press
        self._last_pos = 0
        self._sw_down_at = None
        self._long_fired = False
        self._last_press_at = 0.0
        self._last_rotate_at = 0.0
        self._sw_pin = sw_pin

        if SIMULATE:
            print(f"[encoder] Simulated — slot={slot}")
            return

        ss = _get_seesaw()
        self._enc = rotaryio.IncrementalEncoder(ss, slot)
        self._sw = digitalio.DigitalIO(ss, sw_pin)
        self._sw.direction = Direction.INPUT
        self._sw.pull = Pull.UP
        self._last_pos = self._enc.position
        print(f"[encoder] slot {slot} ready (switch pin {sw_pin})")

    def reset(self):
        """Discard any position accumulated since init. Call before main loop."""
        if SIMULATE:
            return
        self._last_pos = self._enc.position
        self._sw_down_at = None
        self._long_fired = False

    def poll(self):
        """Read encoder position + button. Call from main loop."""
        if SIMULATE:
            return

        now = time.monotonic()

        try:
            # ── rotation ──────────────────────────────────────────────────
            pos = self._enc.position
            if pos != self._last_pos:
                delta = pos - self._last_pos
                # handle 32-bit signed overflow
                if delta > 0x7FFFFFFF:
                    delta -= 0x100000000
                elif delta < -0x80000000:
                    delta += 0x100000000
                self._last_pos = pos
                self._last_rotate_at = now
                self.on_rotate(delta)

            # ── button ────────────────────────────────────────────────────
            pressed = not self._sw.value
            if pressed and self._sw_down_at is None:
                self._sw_down_at = now
                self._long_fired = False
            elif pressed and self._sw_down_at is not None:
                # long-press fires once while still held
                if (
                    not self._long_fired
                    and self.on_long_press is not None
                    and (now - self._sw_down_at) >= LONG_PRESS_S
                    and (now - self._last_press_at) >= MIN_BETWEEN_PRESS_S
                ):
                    self._long_fired = True
                    self._last_press_at = now
                    self.on_long_press()
                # stuck pressed too long = floating pin (seesaw lost pull-up)
                elif (now - self._sw_down_at) > STUCK_PRESS_S:
                    print(f"[encoder] pin {self._sw_pin} stuck — reapplying pull-up", flush=True)
                    self._sw.direction = Direction.INPUT
                    self._sw.pull = Pull.UP
                    self._sw_down_at = None
                    self._long_fired = False
            elif not pressed and self._sw_down_at is not None:
                held = now - self._sw_down_at
                long_fired = self._long_fired
                self._sw_down_at = None
                self._long_fired = False
                if long_fired:
                    return  # long-press already handled; don't also short-press
                if held >= MIN_HOLD_S and (now - self._last_press_at) >= MIN_BETWEEN_PRESS_S:
                    self._last_press_at = now
                    self.on_press()
        except OSError as e:
            print(f"[encoder] I2C error: {e}", flush=True)
