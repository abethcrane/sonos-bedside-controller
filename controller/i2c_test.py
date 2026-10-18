"""Switch pin test with pull-ups enabled."""

import time
from board import SCL, SDA
import busio
from adafruit_seesaw import seesaw, rotaryio, digitalio
from digitalio import Direction, Pull

i2c = busio.I2C(SCL, SDA)
ss = seesaw.Seesaw(i2c, addr=0x49)
print("seesaw connected\n")

enc0 = rotaryio.IncrementalEncoder(ss, 0)
enc2 = rotaryio.IncrementalEncoder(ss, 2)
last0 = enc0.position
last2 = enc2.position

sw0 = digitalio.DigitalIO(ss, 12)
sw0.direction = Direction.INPUT
sw0.pull = Pull.UP

sw2 = digitalio.DigitalIO(ss, 17)
sw2.direction = Direction.INPUT
sw2.pull = Pull.UP

MIN_HOLD_S = 0.05
MIN_BETWEEN_S = 0.30

sw_down_at = {0: None, 2: None}
last_press_at = {0: 0, 2: 0}
switches = {0: sw0, 2: sw2}

t0 = time.monotonic()
print("twist and press both knobs — Ctrl+C to quit\n")

while True:
    now = time.monotonic()
    ts = f"{now - t0:8.3f}"

    pos0 = enc0.position
    if pos0 != last0:
        delta = pos0 - last0
        if delta > 2**31:
            delta -= 2**32
        elif delta < -(2**31):
            delta += 2**32
        print(f"  {ts}  [enc0] rotate  delta={delta}")
        last0 = pos0

    pos2 = enc2.position
    if pos2 != last2:
        delta = pos2 - last2
        if delta > 2**31:
            delta -= 2**32
        elif delta < -(2**31):
            delta += 2**32
        print(f"  {ts}  [enc2] rotate  delta={delta}")
        last2 = pos2

    for slot in [0, 2]:
        pressed = not switches[slot].value
        if pressed and sw_down_at[slot] is None:
            sw_down_at[slot] = now
        if not pressed and sw_down_at[slot] is not None:
            held = now - sw_down_at[slot]
            sw_down_at[slot] = None
            if held >= MIN_HOLD_S and (now - last_press_at[slot]) >= MIN_BETWEEN_S:
                print(f"  {ts}  [enc{slot}] PRESS  (held {held:.3f}s)")
                last_press_at[slot] = now
            elif held < MIN_HOLD_S:
                print(f"  {ts}  [enc{slot}] (bounce: {held*1000:.0f}ms)")

    time.sleep(0.005)
