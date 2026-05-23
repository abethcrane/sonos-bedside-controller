# server.py
from flask import Flask, jsonify, request, abort, render_template_string
import json, os

app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"items": []}
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

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
    save_config({"items": data["items"]})
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
    from sonos import get_household_and_group, get_playlists, get_favorites
    hh_id, _ = get_household_and_group()
    playlists = [{"type": "playlist", "id": p["id"], "name": p["name"]}
                 for p in get_playlists(hh_id)]
    favorites = [{"type": "favorite", "id": f["id"], "name": f["name"]}
                 for f in get_favorites(hh_id)]
    return jsonify({"playlists": playlists, "favorites": favorites})

# --- notify main loop to reload config without restart ---
_reload_flag = False

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
  .item-name { flex: 1; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item-name input { border: none; background: transparent; font: inherit; width: 100%; outline: none; color: inherit; }
  .badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; flex-shrink: 0; }
  .badge-pl { background: #E1F5EE; color: #0F6E56; }
  .badge-fav { background: #EEEDFE; color: #534AB7; }
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
</style>
</head>
<body>
 
<h1>Sonos Beside</h1>
<p class="sub">Drag to reorder · click + to add · click × to remove · double-click name to rename</p>
 
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
 
<script>
let activeItems = [];
let availableItems = [];
let dragIdx = null;
 
async function load() {
  const [configRes, browseRes] = await Promise.all([
    fetch('/items').then(r => r.json()),
    fetch('/browse').then(r => r.json()),
  ]);
 
  activeItems = configRes.items || [];
 
  const activeIds = new Set(activeItems.map(i => i.id));
  const allAvailable = [
    ...(browseRes.favorites || []),
    ...(browseRes.playlists || []),
  ];
  availableItems = allAvailable.filter(i => !activeIds.has(i.id));
 
  renderActive();
  renderAvailable();
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
 
    const label = item.label || item.name || '';
    const badgeClass = item.type === 'playlist' ? 'badge-pl' : 'badge-fav';
    const badgeText = item.type === 'playlist' ? 'playlist' : 'favorite';
 
    div.innerHTML = `
      <span class="handle" title="drag to reorder">⠿</span>
      <span class="item-name"><input value="${escHtml(label)}" title="double-click to edit" readonly
        ondblclick="this.removeAttribute('readonly');this.focus()"
        onblur="commitRename(${idx}, this)"
        onkeydown="if(event.key==='Enter'){this.blur()}"
      /></span>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="rm" title="remove" onclick="removeItem(${idx})">×</button>
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
    div.innerHTML = `
      <span class="item-name">${escHtml(item.name)}</span>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="rm" style="color:#1D9E75;font-size:18px" title="add to device" onclick="addItem(${idx})">+</button>
    `;
    el.appendChild(div);
  });
}
 
function addItem(idx) {
  const item = availableItems.splice(idx, 1)[0];
  activeItems.push({ type: item.type, id: item.id, label: item.name });
  renderActive();
  renderAvailable();
}
 
function removeItem(idx) {
  const item = activeItems.splice(idx, 1)[0];
  availableItems.unshift({ type: item.type, id: item.id, name: item.label || item.name || item.id });
  renderActive();
  renderAvailable();
}
 
function commitRename(idx, input) {
  input.setAttribute('readonly', true);
  activeItems[idx].label = input.value.trim() || activeItems[idx].label;
}
 
async function save() {
  const status = document.getElementById('status');
  status.className = 'status';
  status.textContent = 'Saving...';
  try {
    const res = await fetch('/items', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: activeItems }),
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