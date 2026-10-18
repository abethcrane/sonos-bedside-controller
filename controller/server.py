# server.py
from flask import Flask, jsonify, request, abort, render_template_string
import json, os, re

app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def _default_config():
    return {
        "items": [],
        "room_presets": [],
        "active_room_preset": None,
    }

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return _default_config()
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    data.setdefault("items", [])
    data.setdefault("room_presets", [])
    data.setdefault("active_room_preset", None)
    return data

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

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


UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sonos Beside — config</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f4; color: #1c1c1a; min-height: 100vh; padding: 2rem 1rem; }
  h1 { font-size: 18px; font-weight: 500; margin-bottom: 4px; }
  .sub { font-size: 13px; color: #6b6b67; margin-bottom: 2rem; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; max-width: 860px; }
  @media (max-width: 600px) { .cols { grid-template-columns: 1fr; } }
  .panel { background: #fff; border: 0.5px solid rgba(0,0,0,0.1); border-radius: 12px; padding: 1.25rem; }
  .panel-title { font-size: 12px; font-weight: 500; color: #6b6b67; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
  .item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; border: 0.5px solid rgba(0,0,0,0.08); background: #fafaf9; margin-bottom: 6px; cursor: default; user-select: none; }
  .item.dragging { opacity: 0.4; }
  .item.drag-over { border-color: #378ADD; background: #E6F1FB; }
  .handle { color: #aaa; font-size: 14px; cursor: grab; flex-shrink: 0; }
  .handle:active { cursor: grabbing; }
  .item-name { flex: 1; font-size: 14px; min-width: 0; }
  .item-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item-desc { font-size: 12px; color: #6b6b67; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
  .item-name input { border: none; background: transparent; font: inherit; width: 100%; outline: none; color: inherit; display: block; }
  .badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; flex-shrink: 0; }
  .badge-pl { background: #E1F5EE; color: #0F6E56; }
  .badge-fav { background: #EEEDFE; color: #534AB7; }
  .icon-btn { background: none; border: none; color: #aaa; font-size: 14px; cursor: pointer; flex-shrink: 0; line-height: 1; padding: 2px 4px; border-radius: 4px; }
  .icon-btn:hover { background: rgba(0,0,0,0.05); color: #555; }
  .icon-btn.play:hover { color: #378ADD; }
  .icon-btn.edit:hover { color: #534AB7; }
  .icon-btn.rm:hover { color: #E24B4A; background: none; }
  .icon-btn.playing { color: #378ADD; }
  .rm { background: none; border: none; color: #ccc; font-size: 16px; cursor: pointer; flex-shrink: 0; line-height: 1; padding: 0 2px; }
  .rm:hover { color: #E24B4A; }
  .available .item { cursor: pointer; }
  .available .item:hover { background: #E6F1FB; border-color: #378ADD; }
  .save-bar { max-width: 860px; margin-top: 1.25rem; display: flex; align-items: center; gap: 12px; }
  button.primary { padding: 8px 20px; border-radius: 8px; border: 0.5px solid rgba(0,0,0,0.15); background: #1c1c1a; color: #fff; font-size: 14px; cursor: pointer; }
  button.primary:hover { background: #333; }
  button.primary:active { transform: scale(0.98); }
  .status { font-size: 13px; color: #6b6b67; }
  .status.ok  { color: #1D9E75; }
  .status.err { color: #E24B4A; }
  .empty { font-size: 13px; color: #aaa; padding: 12px 0; text-align: center; }
  .rooms-panel { max-width: 860px; margin-top: 1.5rem; }
  .room-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; margin-bottom: 10px; padding: 10px; border-radius: 8px; border: 0.5px solid rgba(0,0,0,0.08); background: #fafaf9; }
  .room-row.active { border-color: #378ADD; background: #E6F1FB; }
  .room-row input[type=text] { flex: 1; min-width: 120px; font-size: 14px; padding: 6px 8px; border: 0.5px solid rgba(0,0,0,0.12); border-radius: 6px; }
  .room-chips { display: flex; flex-wrap: wrap; gap: 6px; flex: 2; min-width: 180px; }
  .chip { font-size: 12px; padding: 4px 8px; border-radius: 6px; border: 0.5px solid rgba(0,0,0,0.12); background: #fff; cursor: pointer; user-select: none; }
  .chip.on { background: #1c1c1a; color: #fff; border-color: #1c1c1a; }
  .chip.missing { opacity: 0.45; text-decoration: line-through; }
  .room-actions { display: flex; gap: 4px; align-items: center; }
  button.secondary { padding: 6px 12px; border-radius: 8px; border: 0.5px solid rgba(0,0,0,0.15); background: #fff; font-size: 13px; cursor: pointer; }
  button.secondary:hover { background: #f0f0ef; }
</style>
</head>
<body>
 
<h1>Sonos Beside</h1>
<p class="sub">Drag to reorder · ▶ play on Sonos · ✎ rename · + add · × remove</p>
 
<div class="cols">
  <div class="panel">
    <div class="panel-title">On device</div>
    <div id="active-list"></div>
  </div>
  <div class="panel available">
    <div class="panel-title">Available to add</div>
    <div id="available-list"></div>
  </div>
</div>
 
<div class="save-bar">
  <button class="primary" onclick="save()">Save to device</button>
  <span class="status" id="status"></span>
</div>

<div class="panel rooms-panel">
  <div class="panel-title">Room presets</div>
  <p class="sub" style="margin-bottom:12px">Named groups of rooms. Device ROOMS knob scrolls these; press applies and persists.</p>
  <div id="room-presets"></div>
  <div class="save-bar" style="margin-top:12px">
    <button class="secondary" onclick="addPreset()">+ preset</button>
    <button class="primary" onclick="saveRooms()">Save rooms</button>
    <span class="status" id="rooms-status"></span>
  </div>
</div>
 
<script>
let activeItems = [];
let availableItems = [];
let sonosById = {};
let dragIdx = null;
let playingId = null;
let roomPresets = [];
let activeRoomPreset = null;
let discoveredRooms = [];

function mergeSonosMeta(items) {
  return items.map(item => {
    const meta = sonosById[item.id];
    if (!meta) return item;
    return { ...item, name: item.name || meta.name, description: meta.description };
  });
}

function itemTitle(item) {
  return item.label || item.name || item.id;
}

function itemSubtitle(item) {
  if (item.label) return item.description || '';
  return item.description || '';
}

function itemNameHtml(item, idx, editable) {
  const title = itemTitle(item);
  const sub = itemSubtitle(item);
  if (editable) {
    return `<span class="item-name">
      <input id="name-${idx}" value="${escHtml(title)}" title="click ✎ to rename" readonly
        onblur="commitRename(${idx}, this)"
        onkeydown="if(event.key==='Enter'){this.blur()} if(event.key==='Escape'){this.value=itemTitle(activeItems[${idx}]);this.blur()}"
      />
      ${sub ? `<div class="item-desc">${escHtml(sub)}</div>` : ''}
    </span>`;
  }
  return `<span class="item-name">
    <div class="item-title">${escHtml(title)}</div>
    ${sub ? `<div class="item-desc">${escHtml(sub)}</div>` : ''}
  </span>`;
}
 
async function load() {
  const [configRes, browseRes, roomsRes, presetsRes] = await Promise.all([
    fetch('/items').then(r => r.json()),
    fetch('/browse').then(r => r.json()),
    fetch('/rooms').then(r => r.json()).catch(() => ({ rooms: [] })),
    fetch('/room_presets').then(r => r.json()).catch(() => ({ room_presets: [], active_room_preset: null })),
  ]);
 
  sonosById = {};
  for (const item of [...(browseRes.favorites || []), ...(browseRes.playlists || [])]) {
    sonosById[item.id] = item;
  }

  activeItems = mergeSonosMeta(configRes.items || []);
 
  const activeIds = new Set(activeItems.map(i => i.id));
  const allAvailable = [
    ...(browseRes.favorites || []),
    ...(browseRes.playlists || []),
  ];
  availableItems = allAvailable.filter(i => !activeIds.has(i.id));

  discoveredRooms = roomsRes.rooms || [];
  roomPresets = presetsRes.room_presets || configRes.room_presets || [];
  activeRoomPreset = presetsRes.active_room_preset ?? configRes.active_room_preset ?? null;
 
  renderActive();
  renderAvailable();
  renderRooms();
}
 
function renderActive() {
  const el = document.getElementById('active-list');
  if (!activeItems.length) {
    el.innerHTML = '<div class="empty">Nothing here yet — add from the right</div>';
    return;
  }
  el.innerHTML = '';
  activeItems.forEach((item, idx) => {
    const div = document.createElement('div');
    div.className = 'item';
    div.draggable = true;
    div.dataset.idx = idx;
 
    const label = itemTitle(item);
    const badgeClass = item.type === 'playlist' ? 'badge-pl' : 'badge-fav';
    const badgeText = item.type === 'playlist' ? 'playlist' : 'favorite';
 
    const playing = playingId === item.id ? ' playing' : '';
    div.innerHTML = `
      <span class="handle" title="drag to reorder">⠿</span>
      ${itemNameHtml(item, idx, true)}
      <button class="icon-btn play${playing}" title="play on Sonos" onclick="playItem(${idx}, true)">▶</button>
      <button class="icon-btn edit" title="rename" onclick="startRename(${idx})">✎</button>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="icon-btn rm" title="remove" onclick="removeItem(${idx})">×</button>
    `;
 
    div.addEventListener('dragstart', () => { dragIdx = idx; div.classList.add('dragging'); });
    div.addEventListener('dragend',   () => { dragIdx = null; div.classList.remove('dragging'); renderActive(); });
    div.addEventListener('dragover',  e => { e.preventDefault(); div.classList.add('drag-over'); });
    div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
    div.addEventListener('drop', e => {
      e.preventDefault();
      div.classList.remove('drag-over');
      if (dragIdx === null || dragIdx === idx) return;
      const moved = activeItems.splice(dragIdx, 1)[0];
      activeItems.splice(idx, 0, moved);
      dragIdx = null;
      renderActive();
    });
 
    el.appendChild(div);
  });
}
 
function renderAvailable() {
  const el = document.getElementById('available-list');
  if (!availableItems.length) {
    el.innerHTML = '<div class="empty">All favorites and playlists are on device</div>';
    return;
  }
  el.innerHTML = '';
  availableItems.forEach((item, idx) => {
    const div = document.createElement('div');
    div.className = 'item';
    const badgeClass = item.type === 'playlist' ? 'badge-pl' : 'badge-fav';
    const badgeText = item.type === 'playlist' ? 'playlist' : 'favorite';
    const playing = playingId === item.id ? ' playing' : '';
    div.innerHTML = `
      ${itemNameHtml(item, idx, false)}
      <button class="icon-btn play${playing}" title="play on Sonos" onclick="playItem(${idx}, false)">▶</button>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="icon-btn rm" style="color:#1D9E75;font-size:18px" title="add to device" onclick="addItem(${idx})">+</button>
    `;
    el.appendChild(div);
  });
}
 
function addItem(idx) {
  const item = availableItems.splice(idx, 1)[0];
  activeItems.push({ type: item.type, id: item.id, name: item.name, description: item.description });
  renderActive();
  renderAvailable();
}

function removeItem(idx) {
  const item = activeItems.splice(idx, 1)[0];
  availableItems.unshift({
    type: item.type,
    id: item.id,
    name: item.name || item.label || item.id,
    description: item.description,
  });
  renderActive();
  renderAvailable();
}
 
function startRename(idx) {
  const input = document.getElementById(`name-${idx}`);
  if (!input) return;
  input.removeAttribute('readonly');
  input.focus();
  input.select();
}

function commitRename(idx, input) {
  input.setAttribute('readonly', true);
  activeItems[idx].label = input.value.trim() || activeItems[idx].label;
}

async function playItem(idx, onDevice) {
  const item = onDevice ? activeItems[idx] : availableItems[idx];
  const status = document.getElementById('status');
  status.className = 'status';
  status.textContent = `Playing ${itemTitle(item)}…`;
  playingId = item.id;
  renderActive();
  renderAvailable();
  try {
    const res = await fetch('/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: item.type, id: item.id }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Play failed');
    }
    if (data.artist) {
      item.description = data.artist;
      sonosById[item.id] = { ...sonosById[item.id], description: data.artist };
    }
    status.className = 'status ok';
    status.textContent = `Playing on Sonos: ${itemTitle(item)}`;
    renderActive();
    renderAvailable();
  } catch (e) {
    playingId = null;
    renderActive();
    renderAvailable();
    status.className = 'status err';
    status.textContent = `Play failed: ${e.message}`;
  }
}
 
async function save() {
  const status = document.getElementById('status');
  status.className = 'status';
  status.textContent = 'Saving...';
  try {
    const payload = activeItems.map(({ type, id, label }) => {
      const item = { type, id };
      if (label) item.label = label;
      return item;
    });
    const res = await fetch('/items', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: payload }),
    });
    const data = await res.json();
    if (res.ok) {
      await fetch('/reload', { method: 'POST' });
      status.className = 'status ok';
      status.textContent = `Saved ${data.count} item${data.count !== 1 ? 's' : ''} — device updated`;
    } else {
      throw new Error(data.description || 'Unknown error');
    }
  } catch(e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
  }
}
 
function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function slugId(label) {
  return (label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'preset';
}

function renderRooms() {
  const el = document.getElementById('room-presets');
  if (!roomPresets.length) {
    el.innerHTML = '<div class="empty">No presets yet — add one and pick rooms</div>';
    return;
  }
  const known = new Set(discoveredRooms.map(r => r.name));
  el.innerHTML = '';
  roomPresets.forEach((preset, idx) => {
    const div = document.createElement('div');
    div.className = 'room-row' + (preset.id === activeRoomPreset ? ' active' : '');
    const rooms = preset.rooms || [];
    const chips = discoveredRooms.map(r => {
      const on = rooms.includes(r.name) ? ' on' : '';
      return `<span class="chip${on}" onclick="toggleRoom(${idx}, ${JSON.stringify(r.name)})">${escHtml(r.name)}</span>`;
    }).join('');
    const orphans = rooms.filter(n => !known.has(n)).map(n =>
      `<span class="chip on missing" title="not found on Sonos" onclick="toggleRoom(${idx}, ${JSON.stringify(n)})">${escHtml(n)}</span>`
    ).join('');
    div.innerHTML = `
      <input type="text" value="${escHtml(preset.label || '')}" placeholder="Label"
        onchange="roomPresets[${idx}].label=this.value.trim(); if(!roomPresets[${idx}].id) roomPresets[${idx}].id=slugId(this.value)" />
      <div class="room-chips">${chips}${orphans || (discoveredRooms.length ? '' : '<span class="empty">No rooms discovered</span>')}</div>
      <div class="room-actions">
        <button class="secondary" onclick="activatePreset(${idx})" title="apply on device">${preset.id === activeRoomPreset ? 'active' : 'use'}</button>
        <button class="icon-btn rm" title="remove" onclick="removePreset(${idx})">×</button>
      </div>
    `;
    el.appendChild(div);
  });
}

function toggleRoom(idx, name) {
  const rooms = roomPresets[idx].rooms || (roomPresets[idx].rooms = []);
  const i = rooms.indexOf(name);
  if (i >= 0) rooms.splice(i, 1);
  else rooms.push(name);
  renderRooms();
}

function addPreset() {
  let n = roomPresets.length + 1;
  let id = `preset-${n}`;
  while (roomPresets.some(p => p.id === id)) { n += 1; id = `preset-${n}`; }
  roomPresets.push({ id, label: `Preset ${n}`, rooms: [] });
  renderRooms();
}

function removePreset(idx) {
  const removed = roomPresets.splice(idx, 1)[0];
  if (removed && removed.id === activeRoomPreset) activeRoomPreset = null;
  renderRooms();
}

async function activatePreset(idx) {
  const preset = roomPresets[idx];
  const status = document.getElementById('rooms-status');
  status.className = 'status';
  status.textContent = 'Saving & applying…';
  try {
    await saveRooms(false);
    const res = await fetch('/active_room_preset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: preset.id }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.description || data.error || 'Apply failed');
    activeRoomPreset = preset.id;
    renderRooms();
    status.className = 'status ok';
    status.textContent = `Active: ${preset.label}`;
  } catch (e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
  }
}

async function saveRooms(showOk = true) {
  const status = document.getElementById('rooms-status');
  status.className = 'status';
  status.textContent = 'Saving rooms…';
  try {
    const payload = roomPresets.map(p => ({
      id: p.id || slugId(p.label),
      label: (p.label || '').trim() || p.id,
      rooms: p.rooms || [],
    }));
    const res = await fetch('/room_presets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_presets: payload }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.description || 'Unknown error');
    roomPresets = data.room_presets || payload;
    if (data.active_room_preset !== undefined) activeRoomPreset = data.active_room_preset;
    await fetch('/reload', { method: 'POST' });
    renderRooms();
    if (showOk) {
      status.className = 'status ok';
      status.textContent = `Saved ${data.count} preset${data.count !== 1 ? 's' : ''}`;
    }
  } catch (e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
    throw e;
  }
}
 
load();
</script>
</body>
</html>"""

@app.route("/ui")
def ui():
    from flask import render_template_string
    return render_template_string(UI_HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)