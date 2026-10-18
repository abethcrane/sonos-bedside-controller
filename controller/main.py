# controller/main.py
"""
Sonos bedside controller main loop.

Encoder architecture (Pi + USE_ENCODERS):
  - seesaw I2C (encoder.py): hardware quadrature decode, polled from main loop.
  - scroll()/volume(): update selected / volume target under _input_lock, set dirty flags.
  - process_encoder_ui() (~20ms): redraw Sharp from latest state only.
  - Sonos volume: local absolute target (instant UI) + rate-limited absolute sets mid-spin.
    Session soft-cap and VOLUME_MAX stop a fast crank from jumping too loud.

Keyboard/Mac path calls the same actions but renders immediately (no I2C).
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
    set_volume_absolute,
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

# Volume: local absolute target (UI) trails onto Sonos with rate limiting.
_vol_applied = None          # last level we believe Sonos has / last absolute set
_vol_target = None           # where the knob wants to go (instant on screen)
_vol_session_start = None    # level at start of current spin burst (soft-cap anchor)
_vol_pre_base_pct = 0        # % nudge accumulated before get_volume returns
_vol_fetch_inflight = False
_vol_lock = threading.Lock()
_vol_last_detent_at = 0.0
_vol_last_flush_at = 0.0
_vol_api_inflight = False
# Legacy single knob; UP/DOWN override when set.
_vol_pct_legacy = os.environ.get("VOLUME_PCT_PER_DETENT")
VOLUME_PCT_UP = int(os.environ.get("VOLUME_PCT_UP", _vol_pct_legacy or "1"))
VOLUME_PCT_DOWN = int(os.environ.get("VOLUME_PCT_DOWN", _vol_pct_legacy or "2"))
VOLUME_MAX = int(os.environ.get("VOLUME_MAX", "70"))
VOLUME_SESSION_CAP = int(os.environ.get("VOLUME_SESSION_CAP", "12"))
VOLUME_SESSION_IDLE_S = float(os.environ.get("VOLUME_SESSION_IDLE_S", "0.35"))
VOLUME_FLUSH_S = float(os.environ.get("VOLUME_FLUSH_S", "0.08"))
VOLUME_MAX_STEP = int(os.environ.get("VOLUME_MAX_STEP", "4"))
# Target may only lead applied by this much — knob sticks until Sonos catches up.
VOLUME_LEAD_MAX = int(os.environ.get("VOLUME_LEAD_MAX", "8"))
UI_LOOP_S = float(os.environ.get("UI_LOOP_S", "0.02"))
# Cap Sharp playlist redraws — SPI in the main thread was starving pigpio callbacks.
LIST_RENDER_MIN_S = float(os.environ.get("LIST_RENDER_MIN_S", "0.05"))
LIST_RENDER_IDLE_S = float(os.environ.get("LIST_RENDER_IDLE_S", "0.04"))
# Ignore list-encoder press while scrolling or briefly after.
SCROLL_SELECT_GUARD_S = float(os.environ.get("SCROLL_SELECT_GUARD_S", "0.45"))
VOLUME_PLAY_GUARD_S = float(os.environ.get("VOLUME_PLAY_GUARD_S", "0.45"))
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
    global _vol_applied, _vol_target, _vol_session_start, _vol_pre_base_pct
    _vol_applied = None
    _vol_target = None
    _vol_session_start = None
    _vol_pre_base_pct = 0


def _paint_list():
    if not ordered:
        return
    _reset_volume_session()
    display.render_list(ordered, selected)


def _vol_pct_for_delta(delta):
    if delta > 0:
        return VOLUME_PCT_UP
    if delta < 0:
        return VOLUME_PCT_DOWN
    return 0


def _vol_hi_cap(applied=None, target=None):
    """Hard ceiling. Grandfather an already-over-max level; once below MAX, stay ≤ MAX."""
    hi = VOLUME_MAX
    t = target if target is not None else _vol_target
    a = applied if applied is not None else _vol_applied
    base = t if t is not None else a
    if base is not None and base > VOLUME_MAX:
        hi = base
    return hi


def _clamp_target(raw, session_start=None, applied=None, target=None):
    """Apply hard max, session soft-cap (up), and lead limit vs applied."""
    start = session_start if session_start is not None else _vol_session_start
    a = applied if applied is not None else _vol_applied
    hi = _vol_hi_cap(a, target)
    level = max(0, min(hi, int(raw)))
    if start is not None:
        level = min(level, start + VOLUME_SESSION_CAP)
        level = max(0, level)
    # Don't let the on-screen target race the speaker.
    if a is not None and VOLUME_LEAD_MAX >= 0:
        level = max(a - VOLUME_LEAD_MAX, min(a + VOLUME_LEAD_MAX, level))
    return level


def _vol_projected():
    """(session_start, change_from_start, target) for the volume screen."""
    if _vol_target is None:
        return None, _vol_pre_base_pct, None
    start = _vol_session_start if _vol_session_start is not None else _vol_applied
    if start is None:
        return None, _vol_pre_base_pct, None
    return start, _vol_target - start, _vol_target


def _ensure_vol_base():
    """Fetch Sonos volume once per volume session (first knob detent)."""
    global _vol_fetch_inflight
    if _vol_applied is not None or _vol_fetch_inflight or group_id is None:
        return

    def fetch():
        global _vol_applied, _vol_target, _vol_session_start
        global _vol_pre_base_pct, _vol_fetch_inflight, _vol_ui_dirty
        try:
            level = get_volume(group_id)
            with _vol_lock:
                _vol_applied = level
                if _vol_session_start is None:
                    _vol_session_start = level
                pending = _vol_pre_base_pct
                _vol_pre_base_pct = 0
                if _vol_target is None:
                    _vol_target = _clamp_target(level + pending, _vol_session_start, level)
                else:
                    _vol_target = _clamp_target(_vol_target + pending, _vol_session_start, level)
        except Exception as e:
            print(f"Volume read failed: {e}", flush=True)
        finally:
            _vol_fetch_inflight = False
            with _input_lock:
                _vol_ui_dirty = True
            # Knobs may have moved before the read returned — start catching up.
            _flush_volume_if_due()

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
        if now - _scroll_last_detent_at >= SCROLL_SELECT_GUARD_S:
            do_select()
    elif action == "play_pause":
        if now - _vol_last_detent_at >= VOLUME_PLAY_GUARD_S:
            do_play_pause()


def _queue_action(name):
    global _pending_action
    with _input_lock:
        _pending_action = name


def select():
    if USE_ENCODERS:
        if time.monotonic() - _scroll_last_detent_at < SCROLL_SELECT_GUARD_S:
            return
        _queue_action("select")
        return
    do_select()


def do_select():
    # Finish any in-flight volume set, then snap Sonos to the current target.
    while True:
        with _vol_lock:
            if not _vol_api_inflight:
                break
        time.sleep(0.01)
    _flush_volume(force_full=True)
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


def _flush_volume(force_full=False):
    """Move Sonos toward _vol_target (one capped step, or all the way if force_full)."""
    global _vol_applied, _vol_api_inflight, _vol_last_flush_at, _vol_ui_dirty
    with _vol_lock:
        target = _vol_target
        applied = _vol_applied
        if target is None or applied is None or target == applied:
            _vol_api_inflight = False
            return
        if force_full:
            new_level = target
        else:
            diff = target - applied
            step = max(-VOLUME_MAX_STEP, min(VOLUME_MAX_STEP, diff))
            new_level = applied + step
        _vol_api_inflight = True

    msg = f"vol Sonos {new_level} (→ target {_vol_target})"
    display.sim_log(msg)
    print(f"  {msg}", flush=True)
    try:
        set_volume_absolute(group_id, new_level)
        with _vol_lock:
            _vol_applied = new_level
            _vol_last_flush_at = time.monotonic()
            _vol_api_inflight = False
        with _input_lock:
            _vol_ui_dirty = True
    except Exception as e:
        with _vol_lock:
            _vol_api_inflight = False
        print(f"Volume failed: {e}", flush=True)


def _flush_volume_async():
    _flush_volume(force_full=False)


def _flush_volume_if_due():
    """Rate-limited mid-spin apply — ear feedback before the crank goes too far."""
    global _vol_api_inflight
    now = time.monotonic()
    with _vol_lock:
        if (
            _vol_target is None
            or _vol_applied is None
            or _vol_target == _vol_applied
            or _vol_api_inflight
        ):
            return
        if now - _vol_last_flush_at < VOLUME_FLUSH_S:
            return
        _vol_api_inflight = True
    threading.Thread(target=_flush_volume_async, daemon=True).start()


def _nudge_volume_target(delta):
    """Update local absolute target from a signed detent count. Returns True if changed."""
    global _vol_target, _vol_session_start, _vol_pre_base_pct, _vol_last_detent_at
    now = time.monotonic()
    pct = _vol_pct_for_delta(delta) * abs(delta)
    if pct == 0:
        return False
    signed = pct if delta > 0 else -pct

    _ensure_vol_base()

    with _vol_lock:
        # Idle long enough → open a new soft-cap window from current target.
        if (
            _vol_target is not None
            and _vol_last_detent_at > 0
            and (now - _vol_last_detent_at) >= VOLUME_SESSION_IDLE_S
        ):
            _vol_session_start = _vol_target

        _vol_last_detent_at = now

        if _vol_applied is None:
            # Same lead idea before the first Sonos read lands.
            prev = _vol_pre_base_pct
            _vol_pre_base_pct = max(
                -VOLUME_LEAD_MAX,
                min(VOLUME_LEAD_MAX, _vol_pre_base_pct + signed),
            )
            return _vol_pre_base_pct != prev

        if _vol_target is None:
            _vol_target = _vol_applied
        if _vol_session_start is None:
            _vol_session_start = _vol_applied

        new_target = _clamp_target(_vol_target + signed)
        if new_target == _vol_target:
            return False
        _vol_target = new_target
        return True


def volume(delta):
    global _vol_ui_dirty
    changed = _nudge_volume_target(delta)
    if not USE_ENCODERS:
        if changed:
            _paint_volume()
            deadline = time.monotonic() + 1.0
            while _vol_applied is None and time.monotonic() < deadline:
                time.sleep(0.02)
            _flush_volume(force_full=True)
        return
    if changed:
        with _input_lock:
            _vol_ui_dirty = True


def toggle_play_pause():
    if USE_ENCODERS:
        if time.monotonic() - _vol_last_detent_at < VOLUME_PLAY_GUARD_S:
            return
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
                slot=0, sw_pin=12, on_rotate=scroll, on_press=select
            )
            enc_vol = Encoder(
                slot=2, sw_pin=17, on_rotate=volume, on_press=toggle_play_pause
            )
            # discard any position accumulated during Sonos startup
            enc_list.reset()
            enc_vol.reset()

            while True:
                if should_reload():
                    playlists_by_id, favorites_by_id = do_reload()
                enc_list.poll()
                enc_vol.poll()
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