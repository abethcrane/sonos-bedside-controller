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
MIN_PRESS_HOLD_US = 50_000
# Collapse bounce / double edges on the same detent (KY-040 ≈ 20 detents/rev).
MIN_DETENT_INTERVAL_US = 3_000


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
            self.pi.set_glitch_filter(pin, 0)

        # KY-040: one count per detent on CLK rising (DT = direction).
        self._cb_clk = self.pi.callback(clk, pigpio.RISING_EDGE, self._on_clk)
        self._cb_sw = self.pi.callback(sw, pigpio.EITHER_EDGE, self._on_sw)

    def _on_clk(self, gpio, level, tick):
        self._last_rotate_tick = tick
        if tick - self._last_detent_tick < MIN_DETENT_INTERVAL_US:
            return
        self._last_detent_tick = tick
        if self.pi.read(self.dt) == 0:
            self.on_rotate(+1)
        else:
            self.on_rotate(-1)

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
