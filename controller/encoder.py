import os
import sys
import tty
import termios

SIMULATE = not os.path.exists("/dev/gpiomem")

if not SIMULATE:
    import pigpio

DEBOUNCE_US = 250_000
# Ignore button edges briefly after rotation (mechanical / electrical crosstalk).
SW_SUPPRESS_AFTER_ROTATE_US = 80_000

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
        self._sw_suppress_until = 0
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
        # KY-040: SW shorts to GND on press — fire on falling edge, not release/noise highs.
        self._cb_sw = self.pi.callback(sw, pigpio.FALLING_EDGE, self._on_sw)

    def _on_quad(self, gpio, level, tick):
        state = (self.pi.read(self.clk) << 1) | self.pi.read(self.dt)
        if state == self._last_quad_state:
            return
        idx = (self._last_quad_state << 2) | state
        self._last_quad_state = state
        self._quad_accum += _ENCODER_TRANSITIONS[idx]
        if self._quad_accum >= DETENT_PULSES:
            self._quad_accum -= DETENT_PULSES
            self._emit_rotate(+1, tick)
        elif self._quad_accum <= -DETENT_PULSES:
            self._quad_accum += DETENT_PULSES
            self._emit_rotate(-1, tick)

    def _emit_rotate(self, direction, tick):
        self._sw_suppress_until = tick + SW_SUPPRESS_AFTER_ROTATE_US
        self.on_rotate(direction)

    def _on_sw(self, gpio, level, tick):
        if tick < self._sw_suppress_until:
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
