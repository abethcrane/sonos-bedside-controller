# controller/main.py

import json
import os
import sys
import tty
import termios
import threading

# ── detect environment ────────────────────────────────────────────────────────
ON_PI = os.path.exists("/dev/gpiomem")
USE_ENCODERS = ON_PI and os.environ.get("USE_KEYBOARD", "").lower() not in ("1", "true", "yes")

# ── local imports ─────────────────────────────────────────────────────────────
from sonos import (
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
            name = label or favorites_by_id[item_id]["name"]
            resolved.append({"id": item_id, "type": "favorite", "name": name})
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

def fetch_sonos_data():
    global hh_id, group_id, ordered
    print("Connecting to Sonos...")
    hh_id, group_id = get_household_and_group()

    playlists_by_id = {p["id"]: p for p in get_playlists(hh_id)}
    favorites_by_id = {f["id"]: f for f in get_favorites(hh_id)}

    config = load_config()
    ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id)

    if not ordered:
        print("No items in config.json yet.")
        print(f"Run: curl http://localhost:8080/browse to see available playlists/favorites")

    return playlists_by_id, favorites_by_id

# ── actions ───────────────────────────────────────────────────────────────────
def scroll(delta):
    global selected
    if not ordered:
        return
    selected = (selected + delta) % len(ordered)
    arrow = "↑" if delta < 0 else "↓"
    display.sim_log(f"{arrow} {ordered[selected]['name']}")
    display.render_list(ordered, selected)

def select():
    if not ordered:
        return
    item = ordered[selected]
    if item.get("missing"):
        display.sim_log(f"skip (missing on Sonos): {item['name']}")
        if ON_PI:
            print(f"Item '{item['name']}' not found on Sonos — skipping")
        display.render_list(ordered, selected)
        return
    display.sim_log(f"load → {item['name']} ({item['type']})")
    if item["type"] == "playlist":
        load_playlist(group_id, item["id"])
    elif item["type"] == "favorite":
        load_favorite(group_id, item["id"])
    display.render_list(ordered, selected)

def volume(delta):
    display.sim_log("vol +" if delta > 0 else "vol −")
    set_volume(group_id, delta)
    display.render_list(ordered, selected)

def toggle_play_pause():
    display.sim_log("play / pause")
    play_pause(group_id)
    display.render_list(ordered, selected)

# ── config reload (called from Flask /reload endpoint) ───────────────────────
def do_reload(playlists_by_id, favorites_by_id):
    global ordered, selected
    config = load_config()
    ordered = resolve_items(config["items"], playlists_by_id, favorites_by_id)
    selected = min(selected, max(0, len(ordered) - 1))
    display.sim_log("config reloaded")
    display.render_list(ordered, selected)

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
def main():
    global ordered, selected

    # fetch from Sonos
    playlists_by_id, favorites_by_id = fetch_sonos_data()

    # start Flask in background
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, use_reloader=False, threaded=True),
        daemon=True,
    )
    flask_thread.start()
    print("Config server running on http://localhost:8080")

    # initial render
    display.render_list(ordered, selected)

    if not USE_ENCODERS and sys.stdout.isatty():
        print(SIM_HELP)

    if USE_ENCODERS:
        # ── Pi + encoders wired ───────────────────────────────────────────
        enc_list  = Encoder(clk=17, dt=27, sw=22, on_rotate=scroll,   on_press=select)
        enc_vol   = Encoder(clk=5,  dt=26, sw=13, on_rotate=volume,   on_press=toggle_play_pause)

        import time
        while True:
            if should_reload():
                do_reload(playlists_by_id, favorites_by_id)
            time.sleep(0.05)

    else:
        # ── Mac, or Pi with USE_KEYBOARD=1 (no encoders yet) ──────────────
        try:
            while True:
                if should_reload():
                    do_reload(playlists_by_id, favorites_by_id)
                ch = read_key()
                # setraw() clears ISIG — Ctrl+C is \x03 bytes, not SIGINT; Flask's "CTRL+C" hint is wrong here
                if ch in ("q", "\x03", "\x04"):
                    print("\nBye.")
                    break
                fn = KEYS.get(ch)
                if fn:
                    fn()
        except KeyboardInterrupt:
            print("\nBye.")
            sys.exit(0)

if __name__ == "__main__":
    main()