"""
KY-040 rotary encoder input (pigpio GPIO callbacks).

Decoding: strict 4-step sequence FSM (à la codingABI/KY040). Only emits a
detent after the full CW or CCW quadrature path completes at the idle state
(both lines high). Invalid/bounced edges are silently discarded — no
accumulator drift, no time-based same-direction debounce needed.

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

# Valid 4-step quadrature sequences (CLK<<1 | DT at each step).
#   CW:  01 → 00 → 10 → 11
#   CCW: 10 → 00 → 01 → 11
_SEQ_CW  = (0b01, 0b00, 0b10, _IDLE)
_SEQ_CCW = (0b10, 0b00, 0b01, _IDLE)

# Abandon a partial sequence after this many µs with no progress.
SEQUENCE_TIMEOUT_US = int(os.environ.get("ENCODER_SEQ_TIMEOUT_US", "150000"))


def sequence_step(seq_state, new_pin_state, tick):
    """Advance the sequence FSM. Returns (new_seq_state, direction).

    seq_state is a tuple: (step, direction, last_tick)
      step: 0..3 index into _SEQ_CW/_SEQ_CCW, 0 = waiting for first edge
      direction: +1 (CW), -1 (CCW), or 0 (undecided)
      last_tick: pigpio µs tick of last valid transition

    direction result: 0 = no detent yet, +1 = CW detent, -1 = CCW detent.
    """
    step, direction, last_tick = seq_state

    if step > 0 and (tick - last_tick) > SEQUENCE_TIMEOUT_US:
        step, direction = 0, 0

    if new_pin_state == _IDLE and step == 0:
        return (0, 0, tick), 0

    if step == 0:
        if new_pin_state == _SEQ_CW[0]:
            return (1, 1, tick), 0
        if new_pin_state == _SEQ_CCW[0]:
            return (1, -1, tick), 0
        return (0, 0, tick), 0

    if direction == 1:
        expected = _SEQ_CW[step]
    else:
        expected = _SEQ_CCW[step]

    if new_pin_state == expected:
        step += 1
        if step >= 4:
            return (0, 0, tick), direction
        return (step, direction, tick), 0

    # Wrong state — reset only if we're back to idle.
    if new_pin_state == _IDLE:
        return (0, 0, tick), 0
    # Otherwise hold position: might be bounce, real next edge will come.
    return (step, direction, last_tick), 0


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
