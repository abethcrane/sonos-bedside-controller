import os
import sys
import tty
import termios

SIMULATE = not os.path.exists("/dev/gpiomem")

if not SIMULATE:
    import pigpio

DEBOUNCE_US = 250_000
# KY-040 often shorts SW while spinning — require stillness after last detent.
ROTATE_QUIET_BEFORE_PRESS_US = 150_000
# Faster than this between detents → extra steps (menu scroll / volume).
ACCEL_FAST_US = 70_000
ACCEL_MED_US = 140_000
# Ignore tap glitches / detent chatter shorter than this.
MIN_PRESS_HOLD_US = 50_000

# Full quadrature state machine — one detent = 4 valid transitions (not per CLK edge).
_ENCODER_TRANSITIONS = (
    0, -1, 1, 0,
    1, 0, 0, -1,
    -1, 0, 0, 1,
    0, 1, -1, 0,
)
DETENT_PULSES = 4


class Encoder:
    def __init__(self, clk, dt, sw, on_rotate, on_press):
        self.clk = clk
        self.dt = dt
        self.sw = sw
        self.on_rotate = on_rotate
        self.on_press = on_press
        self._last_sw_tick = 0
        self._last_rotate_tick = 0
        self._last_detent_tick = 0
        self._sw_down_tick = 0
        self._quad_accum = 0
        self._last_quad_state = None

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

        self._last_quad_state = (self.pi.read(clk) << 1) | self.pi.read(dt)
        self._cb_clk = self.pi.callback(clk, pigpio.EITHER_EDGE, self._on_quad)
        self._cb_dt = self.pi.callback(dt, pigpio.EITHER_EDGE, self._on_quad)
        # KY-040: SW → GND while held. Count a click on release after min hold + no recent spin.
        self._cb_sw = self.pi.callback(sw, pigpio.EITHER_EDGE, self._on_sw)

    def _on_quad(self, gpio, level, tick):
        # Any CLK/DT edge = knob in motion; blocks SW chatter until fully still.
        self._last_rotate_tick = tick
        state = (self.pi.read(self.clk) << 1) | self.pi.read(self.dt)
        if state == self._last_quad_state:
            return
        idx = (self._last_quad_state << 2) | state
        self._last_quad_state = state
        self._quad_accum += _ENCODER_TRANSITIONS[idx]
        if self._quad_accum >= DETENT_PULSES:
            self._quad_accum -= DETENT_PULSES
            self._emit_detent(+1, tick)
        elif self._quad_accum <= -DETENT_PULSES:
            self._quad_accum += DETENT_PULSES
            self._emit_detent(-1, tick)

    def _emit_detent(self, direction, tick):
        steps = 1
        if self._last_detent_tick:
            gap = tick - self._last_detent_tick
            if gap < ACCEL_FAST_US:
                steps = 3
            elif gap < ACCEL_MED_US:
                steps = 2
        self._last_detent_tick = tick
        self.on_rotate(direction, steps=steps)

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


class KeyboardInput:
    """Mac testing: j/k to scroll, enter to select, space for play/pause, +/- for volume"""

    def __init__(self, on_scroll, on_select, on_playpause, on_volume):
        self.handlers = {
            "k": lambda: on_scroll(-1),
            "j": lambda: on_scroll(+1),
            "\r": on_select,
            " ": on_playpause,
            "+": lambda: on_volume(+1),
            "-": lambda: on_volume(-1),
        }

    def read_key(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch

    def handle(self):
        ch = self.read_key()
        fn = self.handlers.get(ch)
        if fn:
            fn()
        return ch != "q"
