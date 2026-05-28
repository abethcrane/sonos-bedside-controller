import json, os, base64, requests

from sonos_credentials import CLIENT_ID, CLIENT_SECRET

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")
BASE          = "https://api.ws.sonos.com/control/api/v1"
REQUEST_TIMEOUT = 20  # seconds — avoid hanging forever on bad WiFi/DNS


class SonosError(RuntimeError):
    def __init__(self, status, error_code, reason):
        self.status = status
        self.error_code = error_code
        self.reason = reason
        super().__init__(f"{error_code}: {reason}")


def favorite_unsupported(fav):
    """Local library favorites can't be loaded via the cloud Control API."""
    return fav.get("description") == "From Music Library"

def _load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)

def _save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def _refresh():
    tokens = _load_tokens()
    credentials = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        "https://api.sonos.com/login/v3/oauth/access",
        headers={"Authorization": f"Basic {credentials}"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    new_tokens = resp.json()
    if "access_token" not in new_tokens:
        err = new_tokens.get("error", "unknown")
        desc = new_tokens.get("error_description", resp.text)
        raise RuntimeError(f"Sonos token refresh failed ({resp.status_code}): {err} — {desc}")
    new_tokens.setdefault("refresh_token", tokens["refresh_token"])
    _save_tokens(new_tokens)
    return new_tokens["access_token"]

def _get(path):
    tokens = _load_tokens()
    resp = requests.get(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        token = _refresh()
        resp = requests.get(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
    return resp.json()

def _post(path, body=None):
    tokens = _load_tokens()
    resp = requests.post(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json=body or {},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        token = _refresh()
        resp = requests.post(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=body or {},
            timeout=REQUEST_TIMEOUT,
        )
    return resp


def _check_response(resp):
    if resp.status_code < 400:
        return resp
    try:
        data = resp.json()
        code = data.get("errorCode", f"HTTP_{resp.status_code}")
        reason = data.get("reason", resp.text)
    except Exception:
        code = f"HTTP_{resp.status_code}"
        reason = resp.text
    raise SonosError(resp.status_code, code, reason)


def get_household_and_group():
    """Returns (household_id, group_id) for your first household."""
    households = _get("/households")["households"]
    hh_id = households[0]["id"]
    groups = _get(f"/households/{hh_id}/groups")["groups"]
    group_id = groups[0]["id"]
    return hh_id, group_id

def get_playlists(household_id):
    return _get(f"/households/{household_id}/playlists")["playlists"]

def load_playlist(group_id, playlist_id):
    resp = _post(f"/groups/{group_id}/playlists",
                 {"playlistId": playlist_id, "playOnCompletion": True, "action": "REPLACE"})
    _check_response(resp)

def play_pause(group_id):
    _post(f"/groups/{group_id}/playback/togglePlayPause")

def set_volume(group_id, delta_steps):
    """delta_steps: +1 or -1 per encoder detent."""
    _post(f"/groups/{group_id}/groupVolume/relative",
          {"volumeDelta": delta_steps * 2})  # 2% per detent

def get_favorites(household_id):
    return _get(f"/households/{household_id}/favorites")["items"]

def load_favorite(group_id, favorite_id):
    resp = _post(f"/groups/{group_id}/favorites",
                 {"favoriteId": favorite_id, "playOnCompletion": True, "action": "REPLACE"})
    _check_response(resp)