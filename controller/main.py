# controller/main.py
"""
Sonos bedside controller main loop.

Encoder architecture (Pi + USE_ENCODERS):
  - pigpio callbacks (encoder.py): count detents, queue button presses — no SPI/HTTP.
  - scroll()/volume(): update selected / volume state under _input_lock, set dirty flags.
  - process_encoder_ui() (~20ms): redraw Sharp from latest state only.
  - Sonos volume: _vol_pending batched to API on a short timer (network is slow).

Keyboard/Mac path calls the same actions but renders immediately (no GPIO contention).
"""

import json
import os
import signal
import sys
import time
import tty
import termios
import threading

# ── detect environment ────────────────────────────────────────────────────────
ON_PI = os.path.exists("/dev/gpiomem")
USE_ENCODERS = ON_PI and os.environ.get("USE_KEYBOARD", "").lower() not in ("1", "true", "yes")

# ── local imports ─────────────────────────────────────────────────────────────
from sonos import (
    SonosError,
    favorite_unsupported,
    get_household_and_group,
    get_playlists,
    get_favorites,
    load_playlist,
    load_favorite,
    play_pause,
    set_volume,
)
from display import Display, SIM_HELP
from server import app, should_reload

if USE_ENCODERS:
    from encoder import Encoder

# ── config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"items": []}
    with open(CONFIG_FILE) as f:
        return json.load(f)

def resolve_items(config_items, playlists_by_id, favorites_by_id):
    """
    Match config item IDs against what Sonos actually has.
    Falls back to the stored label if Sonos no longer has the item.
    """
    resolved = []
    for entry in config_items:
        item_id = entry["id"]
        item_type = entry["type"]
        label = entry.get("label", "")

        if item_type == "playlist" and item_id in playlists_by_id:
            name = label or playlists_by_id[item_id]["name"]
            resolved.append({"id": item_id, "type": "playlist", "name": name})
        elif item_type == "favorite" and item_id in favorites_by_id:
            fav = favorites_by_id[item_id]
            name = label or fav["name"]
            item = {"id": item_id, "type": "favorite", "name": name}
            if favorite_unsupported(fav):
                item["unsupported"] = True
                item["name"] = f"[local] {name}"
            resolved.append(item)
        else:
            # item not found on Sonos — show it greyed out so you notice
            name = label or f"[missing] {item_id[:8]}"
            resolved.append({"id": item_id, "type": item_type, "name": name, "missing": True})

    return resolved

# ── state ─────────────────────────────────────────────────────────────────────
selected = 0
ordered  = []
hh_id    = None
group_id = None
display  = Display()

_vol_session_delta = 0  # detents since last playlist view (×2 = % on screen)
_vol_pending = 0
_vol_timer = None
_vol_lock = threading.Lock()
_VOL_FLUSH_S = 0.05  # batch Sonos calls — stops the post-spin volume tail

# Encoder input vs display: callbacks touch these; process_encoder_ui() does SPI.
_input_lock = threading.Lock()
_list_ui_dirty = False
_vol_ui_dirty = False
_pending_action = None  # "select" | "play_pause" | None — set from GPIO, run on main thread

def fetch_sonos_data():
    global hh_id, group_id, ordered
    print("Connecting to Sonos...", flush=True)
    print("  households...", flush=True)
    hh_id, group_id = get_household_and_group()
    print("  playlists...", flush=True)
    playlists_by_id = {p["id"]: p for p in get_playlists(hh_id)}
    print("  favorites...", flush=True)
    favorites_by_id = {f["id"]: f for f in get_favorites(hh_id)}
    print("  done.", flush=True)

    config = load_config()
    ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id)

    if not ordered:
        print("No items in config.json yet.")
        print(f"Run: curl http://localhost:8080/browse to see available playlists/favorites")

    return playlists_by_id, favorites_by_id

# ── actions ───────────────────────────────────────────────────────────────────
def _paint_list():
    if not ordered:
        return
    display.sim_log(f"▶ {ordered[selected]['name']}")
    display.render_list(ordered, selected)


def _paint_volume():
    display.render_volume_adjust(_vol_session_delta * 2)


def scroll(delta):
    global selected, _list_ui_dirty
    if not ordered:
        return
    if USE_ENCODERS:
        with _input_lock:
            selected = (selected + delta) % len(ordered)
            _list_ui_dirty = True
        return
    selected = (selected + delta) % len(ordered)
    arrow = "↑" if delta < 0 else "↓"
    display.sim_log(f"{arrow} {ordered[selected]['name']}")
    _paint_list()


def process_encoder_ui():
    """Main-thread refresh: paint latest state; run queued button actions."""
    global _pending_action

    list_dirty = False
    vol_dirty = False
    action = None
    with _input_lock:
        list_dirty = _list_ui_dirty
        _list_ui_dirty = False
        vol_dirty = _vol_ui_dirty
        _vol_ui_dirty = False
        action = _pending_action
        _pending_action = None

    if list_dirty:
        _paint_list()
    elif vol_dirty:
        _paint_volume()

    if action == "select":
        do_select()
    elif action == "play_pause":
        do_play_pause()


def _queue_action(name):
    global _pending_action
    with _input_lock:
        _pending_action = name


def select():
    if USE_ENCODERS:
        _queue_action("select")
        return
    do_select()


def do_select():
    _flush_volume()
    if not ordered:
        return
    item = ordered[selected]
    if item.get("missing"):
        display.sim_log(f"skip (missing on Sonos): {item['name']}")
        if ON_PI:
            print(f"Item '{item['name']}' not found on Sonos — skipping")
        _paint_list()
        return
    if item.get("unsupported"):
        display.sim_log(f"skip (local library): {item['name']}")
        if ON_PI:
            print(f"Item '{item['name']}' is local library — Sonos API can't load it")
        _paint_list()
        return
    display.sim_log(f"load → {item['name']} ({item['type']} id={item['id']})")
    try:
        if item["type"] == "playlist":
            load_playlist(group_id, item["id"])
        elif item["type"] == "favorite":
            load_favorite(group_id, item["id"])
    except SonosError as e:
        display.sim_log(f"load failed: {e.reason}")
        print(f"Sonos load failed ({e.error_code}): {e.reason}", flush=True)
    _paint_list()


def _flush_volume():
    global _vol_pending, _vol_timer
    with _vol_lock:
        batch = _vol_pending
        _vol_pending = 0
        _vol_timer = None
    if batch == 0:
        return
    try:
        set_volume(group_id, batch)
    except Exception as e:
        print(f"Volume failed: {e}", flush=True)


def volume(delta):
    global _vol_session_delta, _vol_pending, _vol_timer, _vol_ui_dirty
    if not USE_ENCODERS:
        _vol_session_delta += delta
        _paint_volume()
        try:
            set_volume(group_id, delta)
        except Exception as e:
            print(f"Volume failed: {e}", flush=True)
        return
    with _input_lock:
        _vol_session_delta += delta
        _vol_ui_dirty = True
    with _vol_lock:
        _vol_pending += delta
        if _vol_timer is not None:
            _vol_timer.cancel()
        _vol_timer = threading.Timer(_VOL_FLUSH_S, _flush_volume)
        _vol_timer.daemon = True
        _vol_timer.start()


def toggle_play_pause():
    if USE_ENCODERS:
        _queue_action("play_pause")
        return
    do_play_pause()


def do_play_pause():
    display.sim_log("play / pause")
    play_pause(group_id)
    _paint_list()

# ── config reload (called from Flask /reload endpoint) ───────────────────────
def do_reload():
    global ordered, selected, hh_id, group_id
    print("Reloading config from Sonos...")
    hh_id, group_id = get_household_and_group()
    playlists_by_id = {p["id"]: p for p in get_playlists(hh_id)}
    favorites_by_id = {f["id"]: f for f in get_favorites(hh_id)}
    config = load_config()
    ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id)
    selected = min(selected, max(0, len(ordered) - 1))
    display.sim_log("config reloaded")
    _paint_list()
    return playlists_by_id, favorites_by_id

# ── keyboard input (Mac simulation) ──────────────────────────────────────────
KEYS = {
    "k": lambda: scroll(-1),   # up
    "j": lambda: scroll(+1),   # down
    "\r": select,              # enter = select
    " ": toggle_play_pause,    # space = play/pause
    "+": lambda: volume(+1),   # volume up
    "-": lambda: volume(-1),   # volume down
}

def read_key():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch

# ── main ──────────────────────────────────────────────────────────────────────
def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    global ordered, selected

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    # fetch from Sonos (runs before any GPIO / encoder setup)
    try:
        playlists_by_id, favorites_by_id = fetch_sonos_data()
    except Exception as e:
        print(f"Sonos startup failed: {e}", flush=True)
        raise

    # start Flask in background
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, use_reloader=False, threaded=True),
        daemon=True,
    )
    flask_thread.start()
    print("Config server running on http://localhost:8080")

    try:
        _paint_list()

        if not USE_ENCODERS and sys.stdout.isatty():
            print(SIM_HELP)

        if USE_ENCODERS:
            enc_list = Encoder(
                clk=17, dt=27, sw=22, on_rotate=scroll, on_press=select
            )
            enc_vol = Encoder(
                clk=5, dt=26, sw=13, on_rotate=volume, on_press=toggle_play_pause
            )

            while True:
                if should_reload():
                    playlists_by_id, favorites_by_id = do_reload()
                process_encoder_ui()
                time.sleep(0.02)

        else:
            # ── Mac, or Pi with USE_KEYBOARD=1 (no encoders yet) ──────────────
            while True:
                if should_reload():
                    playlists_by_id, favorites_by_id = do_reload()
                ch = read_key()
                # setraw() clears ISIG — Ctrl+C is \x03 bytes, not SIGINT
                if ch in ("q", "\x03", "\x04"):
                    print("\nBye.")
                    break
                fn = KEYS.get(ch)
                if fn:
                    fn()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        display.clear()

if __name__ == "__main__":
    main()