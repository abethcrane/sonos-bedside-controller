"""
KY-040 rotary encoder input (pigpio GPIO callbacks).

Decoding: sequence FSM that locks direction from the first non-idle edge,
confirms via 00 (both low), and emits one detent when the encoder returns to
idle (11, both high). Tolerant of skipped intermediate edges — the KY-040
often transitions too fast for the glitch filter or callback to catch all 4
quadrature states.

Callbacks must stay fast (no SPI/HTTP); main.py updates state and runs the
display loop.

Button: release after hold, ignored briefly after rotation (mechanical SW bounce).
"""
import os

SIMULATE = not os.path.exists("/dev/gpiomem")

if not SIMULATE:
    import pigpio

DEBOUNCE_US = 250_000
# KY-040 often shorts SW while spinning — require stillness after last *detent*.
ROTATE_QUIET_BEFORE_PRESS_US = int(os.environ.get("ENCODER_PRESS_QUIET_US", "300000"))
MIN_PRESS_HOLD_US = int(os.environ.get("ENCODER_MIN_PRESS_US", "80000"))

GPIO_GLITCH_FILTER_US = int(os.environ.get("ENCODER_GLITCH_US", "300"))

# Idle state: both CLK and DT high (pull-ups, KY-040 rest position).
_IDLE = 0b11

# Abandon a partial sequence after this many µs with no progress.
SEQUENCE_TIMEOUT_US = int(os.environ.get("ENCODER_SEQ_TIMEOUT_US", "150000"))

# First non-idle edge determines direction:
#   CW  starts with 01 (DT falls first, CLK still high)
#   CCW starts with 10 (CLK falls first, DT still high)
# Confirmation: must see 00 (both low) before returning to idle.
# Emit: on return to idle (11) after confirmed direction.
_DIR_FROM_FIRST_EDGE = {0b01: 1, 0b10: -1}


def sequence_step(seq_state, new_pin_state, tick):
    """Advance the sequence FSM. Returns (new_seq_state, direction).

    seq_state is a tuple: (phase, direction, last_tick)
      phase 0: idle, waiting for first non-idle edge
      phase 1: direction locked, waiting for 00 confirmation
      phase 2: confirmed, waiting for return to idle (11) to emit

    direction result: 0 = no detent yet, +1 = CW detent, -1 = CCW detent.
    """
    phase, direction, last_tick = seq_state

    if phase > 0 and (tick - last_tick) > SEQUENCE_TIMEOUT_US:
        phase, direction = 0, 0

    if phase == 0:
        if new_pin_state in _DIR_FROM_FIRST_EDGE:
            return (1, _DIR_FROM_FIRST_EDGE[new_pin_state], tick), 0
        return (0, 0, tick), 0

    if phase == 1:
        if new_pin_state == 0b00:
            return (2, direction, tick), 0
        if new_pin_state == _IDLE:
            return (0, 0, tick), 0
        return (1, direction, last_tick), 0

    # phase == 2: confirmed, waiting for idle
    if new_pin_state == _IDLE:
        return (0, 0, tick), direction
    return (2, direction, last_tick), 0


class Encoder:
    def __init__(self, clk, dt, sw, on_rotate, on_press):
        self.clk = clk
        self.dt = dt
        self.sw = sw
        self.on_rotate = on_rotate
        self.on_press = on_press
        self._last_sw_tick = 0
        self._last_rotate_tick = 0
        self._sw_down_tick = 0
        self._seq_state = (0, 0, 0)

        if SIMULATE:
            print(f"[encoder] Simulated — GPIO clk={clk} dt={dt} sw={sw}")
            return

        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError(
                "pigpio daemon not running — try: sudo systemctl start pigpiod"
            )

        for pin in (clk, dt, sw):
            self.pi.set_mode(pin, pigpio.INPUT)
            self.pi.set_pull_up_down(pin, pigpio.PUD_UP)
        self.pi.set_glitch_filter(clk, GPIO_GLITCH_FILTER_US)
        self.pi.set_glitch_filter(dt, GPIO_GLITCH_FILTER_US)
        self.pi.set_glitch_filter(sw, 0)

        self._cb_clk = self.pi.callback(clk, pigpio.EITHER_EDGE, self._on_quad)
        self._cb_dt = self.pi.callback(dt, pigpio.EITHER_EDGE, self._on_quad)
        self._cb_sw = self.pi.callback(sw, pigpio.EITHER_EDGE, self._on_sw)

    def _read_pin_state(self):
        clk1, dt1 = self.pi.read(self.clk), self.pi.read(self.dt)
        clk2, dt2 = self.pi.read(self.clk), self.pi.read(self.dt)
        if clk1 != clk2 or dt1 != dt2:
            clk2, dt2 = self.pi.read(self.clk), self.pi.read(self.dt)
        return (clk2 << 1) | dt2

    def _on_quad(self, gpio, level, tick):
        pin_state = self._read_pin_state()
        self._seq_state, direction = sequence_step(
            self._seq_state, pin_state, tick
        )
        if not direction:
            return
        self._last_rotate_tick = tick
        self.on_rotate(direction)

    def _on_sw(self, gpio, level, tick):
        if level == 0:
            self._sw_down_tick = tick
            return
        if not self._sw_down_tick:
            return
        held_us = tick - self._sw_down_tick
        self._sw_down_tick = 0
        if held_us < MIN_PRESS_HOLD_US:
            return
        if tick - self._last_rotate_tick < ROTATE_QUIET_BEFORE_PRESS_US:
            return
        if tick - self._last_sw_tick < DEBOUNCE_US:
            return
        self._last_sw_tick = tick
        self.on_press()
