import os
import sys
import tty
import termios

SIMULATE = not os.path.exists("/dev/gpiomem")

if not SIMULATE:
    import pigpio

DEBOUNCE_US = 250_000


class Encoder:
    def __init__(self, clk, dt, sw, on_rotate, on_press):
        self.clk = clk
        self.dt = dt
        self.sw = sw
        self.on_rotate = on_rotate
        self.on_press = on_press
        self._last_sw_tick = 0

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

        self._cb_clk = self.pi.callback(clk, pigpio.RISING_EDGE, self._on_clk)
        self._cb_sw = self.pi.callback(sw, pigpio.EITHER_EDGE, self._on_sw)

    def _on_clk(self, gpio, level, tick):
        if self.pi.read(self.dt) == 0:
            self.on_rotate(+1)
        else:
            self.on_rotate(-1)

    def _on_sw(self, gpio, level, tick):
        if level == 0:
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
