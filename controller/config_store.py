"""
Persistent device config (items, room_presets, active_room_preset).

On the Pi we prefer ~/.sonos-beside/config.json so `git pull` never
overwrites your playlists/rooms. Mac/dev keeps using controller/config.json
unless SONOS_CONFIG_FILE is set.
"""

import json
import os
import shutil

_DIR = os.path.dirname(__file__)
_REPO_CONFIG = os.path.join(_DIR, "config.json")
_EXAMPLE = os.path.join(_DIR, "config.example.json")
_HOME_CONFIG = os.path.expanduser("~/.sonos-beside/config.json")


def _on_pi():
    return os.path.exists("/dev/gpiomem")


def config_path():
    env = os.environ.get("SONOS_CONFIG_FILE")
    if env:
        return os.path.expanduser(env)
    if _on_pi():
        return _HOME_CONFIG
    return _REPO_CONFIG


def _default_config():
    return {
        "items": [],
        "room_presets": [],
        "active_room_preset": None,
    }


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _seed_if_missing(path):
    """Create path from repo config / example if it doesn't exist yet."""
    if os.path.exists(path):
        return
    _ensure_parent(path)
    # Migrate once: copy the in-repo file if it has anything useful
    if path != _REPO_CONFIG and os.path.exists(_REPO_CONFIG):
        try:
            with open(_REPO_CONFIG) as f:
                data = json.load(f)
            if data.get("items") or data.get("room_presets"):
                shutil.copy2(_REPO_CONFIG, path)
                print(f"[config] migrated {_REPO_CONFIG} → {path}", flush=True)
                return
        except Exception as e:
            print(f"[config] migrate skipped: {e}", flush=True)
    if os.path.exists(_EXAMPLE):
        shutil.copy2(_EXAMPLE, path)
        print(f"[config] seeded {path} from example", flush=True)
        return
    with open(path, "w") as f:
        json.dump(_default_config(), f, indent=2)
    print(f"[config] created empty {path}", flush=True)


def load_config():
    path = config_path()
    _seed_if_missing(path)
    with open(path) as f:
        data = json.load(f)
    data.setdefault("items", [])
    data.setdefault("room_presets", [])
    data.setdefault("active_room_preset", None)
    return data


def save_config(config):
    path = config_path()
    _ensure_parent(path)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
