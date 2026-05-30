# controller/main.py
"""
Sonos bedside controller main loop.

Encoder architecture (Pi + USE_ENCODERS):
  - pigpio callbacks (encoder.py): count detents, queue button presses — no SPI/HTTP.
  - scroll()/volume(): update selected / volume state under _input_lock, set dirty flags.
  - process_encoder_ui() (~20ms): redraw Sharp from latest state only.
  - Sonos volume: _vol_pending flushed on the same ~20ms loop as the display (batched API).

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
from artist_cache import load_artist_cache
from sonos import (
    SonosError,
    cache_artist_from_playback,
    display_name,
    favorite_unsupported,
    get_household_and_group,
    get_playlists,
    get_favorites,
    item_artist_name,
    load_playlist,
    load_favorite,
    play_pause,
    resolve_favorite_artists,
    get_volume,
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

def resolve_items(config_items, playlists_by_id, favorites_by_id, cache=None):
    """
    Match config item IDs against what Sonos actually has.
    Falls back to the stored label if Sonos no longer has the item.
    """
    cache = cache if cache is not None else load_artist_cache()
    resolved = []
    for entry in config_items:
        item_id = entry["id"]
        item_type = entry["type"]
        label = entry.get("label", "")

        if item_type == "playlist" and item_id in playlists_by_id:
            pl = playlists_by_id[item_id]
            artist = item_artist_name(pl, cache)
            name = display_name(pl["name"], pl.get("description"), label, artist=artist or "")
            resolved.append({"id": item_id, "type": "playlist", "name": name})
        elif item_type == "favorite" and item_id in favorites_by_id:
            fav = favorites_by_id[item_id]
            artist = item_artist_name(fav, cache)
            name = display_name(fav["name"], fav.get("description"), label, artist=artist or "")
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

_vol_session_delta = 0  # detents since last volume read or Sonos flush
_vol_base = None  # Sonos group volume at start of current adjustment burst
_vol_fetch_inflight = False
_vol_pending = 0
_vol_lock = threading.Lock()
_vol_last_detent_at = 0.0
_vol_api_inflight = False
# Wait this long after the last detent before sending batched steps to Sonos.
VOLUME_SPIN_IDLE_S = float(os.environ.get("VOLUME_SPIN_IDLE_S", "0.05"))
VOLUME_PCT_PER_DETENT = int(os.environ.get("VOLUME_PCT_PER_DETENT", "2"))
UI_LOOP_S = float(os.environ.get("UI_LOOP_S", "0.02"))
# Cap Sharp playlist redraws — SPI in the main thread was starving pigpio callbacks.
LIST_RENDER_MIN_S = float(os.environ.get("LIST_RENDER_MIN_S", "0.05"))
LIST_RENDER_IDLE_S = float(os.environ.get("LIST_RENDER_IDLE_S", "0.04"))
# Boot: WiFi/Sonos may not be ready the instant systemd starts us.
STARTUP_RETRY_S = float(os.environ.get("STARTUP_RETRY_S", "10"))
STARTUP_MAX_RETRIES = int(os.environ.get("STARTUP_MAX_RETRIES", "0"))  # 0 = retry forever

# Encoder input vs display: callbacks touch these; process_encoder_ui() does SPI.
_input_lock = threading.Lock()
_list_ui_dirty = False
_vol_ui_dirty = False
_scroll_last_detent_at = 0.0
_last_list_render_at = 0.0
_pending_action = None  # "select" | "play_pause" | None — set from GPIO, run on main thread

def fetch_sonos_data():
    global hh_id, group_id, ordered
    print("Connecting to Sonos...", flush=True)
    print("  households...", flush=True)
    hh_id, group_id = get_household_and_group()
    print("  playlists...", flush=True)
    playlists_by_id = {p["id"]: p for p in get_playlists(hh_id)}
    print("  favorites...", flush=True)
    favorites_list = get_favorites(hh_id)
    cache = resolve_favorite_artists(favorites_list)
    favorites_by_id = {f["id"]: f for f in favorites_list}
    print("  done.", flush=True)

    config = load_config()
    ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id, cache)

    if not ordered:
        print("No items in config.json yet.")
        print(f"Run: curl http://localhost:8080/browse to see available playlists/favorites")

    return playlists_by_id, favorites_by_id


def fetch_sonos_data_with_retry():
    """Keep trying until Sonos is reachable (WiFi/DNS often lag boot by 30–60s)."""
    attempt = 0
    while True:
        try:
            return fetch_sonos_data()
        except Exception as e:
            attempt += 1
            if STARTUP_MAX_RETRIES and attempt >= STARTUP_MAX_RETRIES:
                raise
            print(f"Sonos startup failed (attempt {attempt}): {e}", flush=True)
            print(f"Retrying in {STARTUP_RETRY_S:.0f}s...", flush=True)
            time.sleep(STARTUP_RETRY_S)

# ── actions ───────────────────────────────────────────────────────────────────
def _reset_volume_session():
    global _vol_base, _vol_session_delta
    _vol_base = None
    _vol_session_delta = 0


def _paint_list():
    if not ordered:
        return
    _reset_volume_session()
    display.render_list(ordered, selected)


def _vol_change_pct():
    return _vol_session_delta * VOLUME_PCT_PER_DETENT


def _vol_projected():
    change = _vol_change_pct()
    if _vol_base is None:
        return None, change, None
    new = max(0, min(100, _vol_base + change))
    return _vol_base, change, new


def _ensure_vol_base():
    """Fetch Sonos volume once per volume session (first knob detent)."""
    global _vol_fetch_inflight
    if _vol_base is not None or _vol_fetch_inflight or group_id is None:
        return

    def fetch():
        global _vol_base, _vol_fetch_inflight, _vol_ui_dirty
        try:
            _vol_base = get_volume(group_id)
        except Exception as e:
            print(f"Volume read failed: {e}", flush=True)
        finally:
            _vol_fetch_inflight = False
            with _input_lock:
                _vol_ui_dirty = True

    _vol_fetch_inflight = True
    threading.Thread(target=fetch, daemon=True).start()


def _paint_volume():
    current, change, new = _vol_projected()
    display.render_volume_adjust(current, change, new)


def scroll(delta):
    global selected, _list_ui_dirty, _scroll_last_detent_at
    if not ordered:
        return
    if USE_ENCODERS:
        with _input_lock:
            selected = (selected + delta) % len(ordered)
            _list_ui_dirty = True
        _scroll_last_detent_at = time.monotonic()
        return
    selected = (selected + delta) % len(ordered)
    arrow = "↑" if delta < 0 else "↓"
    display.sim_log(f"{arrow} {ordered[selected]['name']}")
    _paint_list()


def process_encoder_ui():
    """Main-thread refresh: paint latest state; run queued button actions."""
    global _pending_action, _list_ui_dirty, _vol_ui_dirty, _last_list_render_at

    now = time.monotonic()
    list_dirty = False
    vol_dirty = False
    action = None
    with _input_lock:
        list_dirty = _list_ui_dirty
        vol_dirty = _vol_ui_dirty
        _vol_ui_dirty = False
        action = _pending_action
        _pending_action = None

    if list_dirty and ordered:
        scroll_idle = now - _scroll_last_detent_at >= LIST_RENDER_IDLE_S
        render_due = now - _last_list_render_at >= LIST_RENDER_MIN_S
        if scroll_idle or render_due:
            with _input_lock:
                _list_ui_dirty = False
            display.sim_log(f"▶ {ordered[selected]['name']}")
            _paint_list()
            _last_list_render_at = now
    elif vol_dirty:
        _paint_volume()

    _flush_volume_if_due()

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
    # Apply any volume detents still waiting for spin-idle flush.
    while True:
        with _vol_lock:
            if not _vol_api_inflight:
                break
        time.sleep(0.01)
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
            time.sleep(0.8)
            cache_artist_from_playback(group_id, item["id"])
    except SonosError as e:
        display.sim_log(f"load failed: {e.reason}")
        print(f"Sonos load failed ({e.error_code}): {e.reason}", flush=True)
    _paint_list()


def _flush_volume():
    """Send accumulated detents to Sonos (background thread or before select)."""
    global _vol_pending, _vol_api_inflight, _vol_base, _vol_session_delta, _vol_ui_dirty
    with _vol_lock:
        batch = _vol_pending
        _vol_pending = 0
        _vol_api_inflight = False
    if batch == 0:
        return
    pct = batch * VOLUME_PCT_PER_DETENT
    if _vol_base is not None:
        msg = f"vol Sonos {max(0, min(100, _vol_base + pct))} ({pct:+d}%)"
    else:
        msg = f"vol Sonos Δ{pct:+d}% ({abs(batch)} detent{'s' if abs(batch) != 1 else ''})"
    display.sim_log(msg)
    print(f"  {msg}", flush=True)
    try:
        set_volume(group_id, batch)
        if _vol_base is not None:
            _vol_base = max(0, min(100, _vol_base + pct))
        _vol_session_delta = 0
        with _input_lock:
            _vol_ui_dirty = True
    except Exception as e:
        print(f"Volume failed: {e}", flush=True)


def _flush_volume_async():
    global _vol_api_inflight
    with _vol_lock:
        if _vol_pending == 0:
            _vol_api_inflight = False
            return
    _flush_volume()


def _flush_volume_if_due():
    """After knob pauses, send full pending batch (not one detent mid-spin)."""
    global _vol_api_inflight
    now = time.monotonic()
    with _vol_lock:
        pending = _vol_pending
        if pending == 0 or _vol_api_inflight:
            return
    if now - _vol_last_detent_at < VOLUME_SPIN_IDLE_S:
        return
    with _vol_lock:
        _vol_api_inflight = True
    threading.Thread(target=_flush_volume_async, daemon=True).start()


def volume(delta):
    global _vol_session_delta, _vol_ui_dirty, _vol_pending, _vol_last_detent_at, _vol_base
    _ensure_vol_base()
    if not USE_ENCODERS:
        _vol_session_delta += delta
        _paint_volume()
        try:
            set_volume(group_id, delta)
            pct = delta * VOLUME_PCT_PER_DETENT
            if _vol_base is not None:
                _vol_base = max(0, min(100, _vol_base + pct))
                _vol_session_delta = 0
        except Exception as e:
            print(f"Volume failed: {e}", flush=True)
        return
    with _input_lock:
        _vol_session_delta += delta
        _vol_ui_dirty = True
    with _vol_lock:
        _vol_pending += delta
    _vol_last_detent_at = time.monotonic()


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
    print("Reloading config from Sonos...", flush=True)
    try:
        hh_id, group_id = get_household_and_group()
        playlists_by_id = {p["id"]: p for p in get_playlists(hh_id)}
        favorites_list = get_favorites(hh_id)
        cache = resolve_favorite_artists(favorites_list)
        favorites_by_id = {f["id"]: f for f in favorites_list}
        config = load_config()
        ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id, cache)
        selected = min(selected, max(0, len(ordered) - 1))
        display.sim_log("config reloaded")
        _paint_list()
        return playlists_by_id, favorites_by_id
    except Exception as e:
        print(f"Config reload failed: {e}", flush=True)
        display.sim_log(f"reload failed: {e}")
        return None, None

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
    playlists_by_id, favorites_by_id = fetch_sonos_data_with_retry()

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
                with _input_lock:
                    sel = selected
                    items = ordered
                if items and display.advance_marquee(sel, items):
                    _paint_list()
                time.sleep(UI_LOOP_S)

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
        display.show_goodbye()

if __name__ == "__main__":
    main()