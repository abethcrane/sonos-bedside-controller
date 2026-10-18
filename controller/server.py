# server.py
from flask import Flask, jsonify, request, abort, redirect, send_from_directory
import os, re

from config_store import config_path, load_config, save_config

app = Flask(__name__)
CONFIG_FILE = config_path()  # for logs / debugging
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# Bump when shipping UI asset changes (also wired into index.html query strings)
UI_ASSET_VER = "cBQW9UjLPjj6T1B8GlIHmUihDYWcqYug.2"

def _slug_id(label):
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return slug or "preset"

def _validate_room_presets(presets):
    if not isinstance(presets, list):
        abort(400, "room_presets must be an array")
    seen = set()
    cleaned = []
    for p in presets:
        if not isinstance(p, dict):
            abort(400, "Each room preset must be an object")
        label = (p.get("label") or "").strip()
        rooms = p.get("rooms")
        if not isinstance(rooms, list) or not all(isinstance(r, str) for r in rooms):
            abort(400, "Each preset needs a 'rooms' string array")
        pid = (p.get("id") or "").strip() or _slug_id(label)
        if pid in seen:
            abort(400, f"Duplicate room preset id: {pid}")
        seen.add(pid)
        cleaned.append({
            "id": pid,
            "label": label or pid,
            "rooms": [r.strip() for r in rooms if r and r.strip()],
        })
    return cleaned

# --- GET current list ---
@app.route("/items", methods=["GET"])
def get_items():
    return jsonify(load_config())

# --- REPLACE entire list (your primary edit endpoint) ---
@app.route("/items", methods=["PUT"])
def put_items():
    data = request.get_json(silent=True)
    if not data or "items" not in data:
        abort(400, "Expected JSON body with 'items' array")
    for item in data["items"]:
        if "type" not in item or "id" not in item:
            abort(400, "Each item needs 'type' and 'id'")
        if item["type"] not in ("playlist", "favorite"):
            abort(400, f"Unknown type: {item['type']}")
    config = load_config()
    config["items"] = data["items"]
    save_config(config)
    return jsonify({"ok": True, "count": len(data["items"])})

# --- ADD a single item (append or insert at position) ---
@app.route("/items", methods=["POST"])
def post_item():
    data = request.get_json(silent=True)
    if not data or "type" not in data or "id" not in data:
        abort(400, "Expected JSON body with 'type' and 'id'")
    config = load_config()
    item = {
        "type": data["type"],
        "id":   data["id"],
        "label": data.get("label", ""),
    }
    position = data.get("position")  # optional — inserts at index if provided
    if position is not None:
        config["items"].insert(int(position), item)
    else:
        config["items"].append(item)
    save_config(config)
    return jsonify({"ok": True, "item": item})

# --- DELETE by id ---
@app.route("/items/<item_id>", methods=["DELETE"])
def delete_item(item_id):
    config = load_config()
    before = len(config["items"])
    config["items"] = [i for i in config["items"] if i["id"] != item_id]
    if len(config["items"]) == before:
        abort(404, f"No item with id '{item_id}'")
    save_config(config)
    return jsonify({"ok": True})

# --- REORDER: move item to new index ---
@app.route("/items/<item_id>/move", methods=["POST"])
def move_item(item_id):
    data = request.get_json(silent=True)
    if not data or "position" not in data:
        abort(400, "Expected JSON body with 'position'")
    config = load_config()
    items = config["items"]
    matches = [i for i in items if i["id"] == item_id]
    if not matches:
        abort(404, f"No item with id '{item_id}'")
    item = matches[0]
    items.remove(item)
    items.insert(int(data["position"]), item)
    save_config(config)
    return jsonify({"ok": True, "items": items})

# --- UPDATE label override ---
@app.route("/items/<item_id>", methods=["PATCH"])
def patch_item(item_id):
    data = request.get_json(silent=True)
    config = load_config()
    for item in config["items"]:
        if item["id"] == item_id:
            if "label" in data:
                item["label"] = data["label"]
            save_config(config)
            return jsonify({"ok": True, "item": item})
    abort(404, f"No item with id '{item_id}'")

# --- BROWSE available Sonos playlists and favorites (for discovery) ---
@app.route("/browse", methods=["GET"])
def browse():
    from sonos import (
        get_household_and_group,
        get_playlists,
        get_favorites,
        resolve_favorite_artists,
        sonos_item_context,
    )
    hh_id, _ = get_household_and_group()
    playlists = []
    for p in get_playlists(hh_id):
        item = {"type": "playlist", "id": p["id"], "name": p["name"]}
        ctx = sonos_item_context(p)
        if ctx:
            item["description"] = ctx
        playlists.append(item)
    favorites_raw = get_favorites(hh_id)
    cache = resolve_favorite_artists(favorites_raw)
    favorites = []
    for f in favorites_raw:
        item = {"type": "favorite", "id": f["id"], "name": f["name"]}
        ctx = sonos_item_context(f, cache)
        if ctx:
            item["description"] = ctx
        favorites.append(item)
    return jsonify({"playlists": playlists, "favorites": favorites})

# --- PLAY a playlist or favorite on the Sonos group (preview from UI) ---
@app.route("/play", methods=["POST"])
def play_item():
    import time
    from sonos import (
        SonosError,
        cache_artist_from_playback,
        favorite_unsupported,
        get_favorites,
        get_household_and_group,
        load_favorite,
        load_playlist,
    )
    data = request.get_json(silent=True)
    if not data or "type" not in data or "id" not in data:
        return jsonify({"ok": False, "error": "Expected JSON body with 'type' and 'id'"}), 400
    item_type = data["type"]
    item_id = data["id"]
    if item_type not in ("playlist", "favorite"):
        return jsonify({"ok": False, "error": f"Unknown type: {item_type}"}), 400
    try:
        hh_id, group_id = get_household_and_group()
        if item_type == "favorite":
            fav = next((f for f in get_favorites(hh_id) if f["id"] == item_id), None)
            if fav and favorite_unsupported(fav):
                return jsonify({
                    "ok": False,
                    "error": "Local library favorites can't be played via the Sonos API",
                }), 400
        if item_type == "playlist":
            load_playlist(group_id, item_id)
        else:
            load_favorite(group_id, item_id)
            time.sleep(0.8)
            cache = cache_artist_from_playback(group_id, item_id)
            artist = cache.get("byFavoriteId", {}).get(str(item_id))
            return jsonify({"ok": True, "artist": artist})
    except SonosError as e:
        return jsonify({"ok": False, "error": f"{e.error_code}: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": True})

# --- notify main loop to reload / apply rooms ---
_reload_flag = False
_apply_rooms_flag = False

@app.route("/reload", methods=["POST"])
def reload_config():
    global _reload_flag
    _reload_flag = True
    return jsonify({"ok": True})

def should_reload():
    global _reload_flag
    if _reload_flag:
        _reload_flag = False
        return True
    return False

def should_apply_rooms():
    """True once after /active_room_preset (consume flag)."""
    global _apply_rooms_flag
    if _apply_rooms_flag:
        _apply_rooms_flag = False
        return True
    return False

# --- discover Sonos rooms/players ---
@app.route("/rooms", methods=["GET"])
def get_rooms():
    from sonos import get_household_and_group, get_players
    try:
        hh_id, _ = get_household_and_group()
        players = [
            {"id": p.get("id"), "name": p.get("name", "")}
            for p in get_players(hh_id)
            if p.get("id")
        ]
        players.sort(key=lambda p: (p["name"] or "").lower())
        return jsonify({"rooms": players})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

# --- room presets ---
@app.route("/room_presets", methods=["GET"])
def get_room_presets():
    config = load_config()
    return jsonify({
        "room_presets": config.get("room_presets", []),
        "active_room_preset": config.get("active_room_preset"),
    })

@app.route("/room_presets", methods=["PUT"])
def put_room_presets():
    data = request.get_json(silent=True)
    if not data or "room_presets" not in data:
        abort(400, "Expected JSON body with 'room_presets' array")
    cleaned = _validate_room_presets(data["room_presets"])
    config = load_config()
    config["room_presets"] = cleaned
    active = config.get("active_room_preset")
    if active and active not in {p["id"] for p in cleaned}:
        config["active_room_preset"] = None
    save_config(config)
    return jsonify({
        "ok": True,
        "count": len(cleaned),
        "room_presets": cleaned,
        "active_room_preset": config.get("active_room_preset"),
    })

@app.route("/active_room_preset", methods=["POST"])
def post_active_room_preset():
    """Set active preset id and ask the device loop to apply it."""
    global _reload_flag, _apply_rooms_flag
    data = request.get_json(silent=True) or {}
    preset_id = data.get("id")
    if not preset_id:
        abort(400, "Expected JSON body with 'id'")
    config = load_config()
    ids = {p["id"] for p in config.get("room_presets", [])}
    if preset_id not in ids:
        abort(404, f"No room preset with id '{preset_id}'")
    config["active_room_preset"] = preset_id
    save_config(config)
    _reload_flag = True
    _apply_rooms_flag = True
    return jsonify({"ok": True, "active_room_preset": preset_id})


def _send_ui_file(filename, *, immutable=False):
    resp = send_from_directory(STATIC_DIR, filename)
    resp.headers["ETag"] = f'"{UI_ASSET_VER}-{filename}"'
    if immutable:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp

# UI lives as static files under controller/static/ (open index.html via file:// for design;
# under Flask, /ui/ keeps relative css/js paths working).
@app.route("/ui")
def ui_redirect():
    return redirect("/ui/", code=302)

@app.route("/ui/")
def ui():
    return _send_ui_file("index.html")

@app.route("/ui/<path:filename>")
def ui_asset(filename):
    # versioned query string is ignored by Flask path; long-cache fingerprinted assets
    immutable = filename in ("ui.css", "ui.js", "favicon.svg")
    return _send_ui_file(filename, immutable=immutable)

@app.route("/favicon.svg")
def favicon_svg():
    return _send_ui_file("favicon.svg", immutable=True)

@app.route("/favicon.ico")
def favicon_ico():
    return _send_ui_file("favicon.svg", immutable=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
