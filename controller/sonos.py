import json, os, base64, re, requests

from sonos_credentials import CLIENT_ID, CLIENT_SECRET
from artist_cache import load_artist_cache, save_artist_cache

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

_GENERIC_FAVORITE_NAMES = frozenset({
    "top tracks", "top track", "radio", "shuffle", "station",
    "mix", "mixes", "on tour", "discover",
})

def is_useless_description(desc):
    d = (desc or "").strip().lower()
    if not d or d == "from music library":
        return True
    if d.endswith(" playlist"):
        return True
    return False

def spotify_artist_id_from_item(item):
    resource = item.get("resource") or {}
    obj_id = (resource.get("id") or {}).get("objectId") or ""
    if "spotify:" in obj_id:
        obj_id = obj_id[obj_id.index("spotify:"):]
    match = re.search(
        r"spotify:artistTopTracks:([A-Za-z0-9]+)|spotify:artist:([A-Za-z0-9]+)",
        obj_id,
    )
    if not match:
        return None
    return match.group(1) or match.group(2)

def is_generic_favorite_name(name):
    n = (name or "").lower().strip()
    return n in _GENERIC_FAVORITE_NAMES or n.startswith("top track")

def display_name(name, description="", label="", artist=""):
    """Build a label from Sonos name + artist/context."""
    if label:
        return label
    name = name or ""
    art = (artist or "").strip()
    if not art:
        desc = (description or "").strip()
        if desc and not is_useless_description(desc):
            art = desc
    if art and is_generic_favorite_name(name):
        return art
    if art and art.lower() not in name.lower():
        return f"{name} · {art}"
    return name

def item_artist_name(item, cache=None):
    """Best-effort artist/context for a Sonos favorite or playlist."""
    cache = cache if cache is not None else load_artist_cache()
    fav_id = item.get("id")
    if fav_id:
        cached = cache.get("byFavoriteId", {}).get(str(fav_id))
        if cached:
            return cached

    spotify_id = spotify_artist_id_from_item(item)
    if spotify_id:
        cached = cache.get("bySpotifyArtistId", {}).get(spotify_id)
        if cached:
            return cached

    desc = (item.get("description") or "").strip()
    if desc and not is_useless_description(desc):
        return desc
    return None

def resolve_favorite_artists(favorites, cache=None):
    """Fill cache from Spotify IDs embedded in Sonos favorites."""
    from spotify import get_artists, spotify_configured

    cache = cache if cache is not None else load_artist_cache()
    by_spotify = cache.setdefault("bySpotifyArtistId", {})
    by_fav = cache.setdefault("byFavoriteId", {})
    missing = set()

    for fav in favorites:
        fav_id = str(fav.get("id", ""))
        if fav_id and fav_id in by_fav:
            continue
        spotify_id = spotify_artist_id_from_item(fav)
        if spotify_id and spotify_id not in by_spotify:
            missing.add(spotify_id)

    if missing and spotify_configured():
        by_spotify.update(get_artists(sorted(missing)))
        save_artist_cache(cache)

    return cache

def sonos_item_context(item, cache=None):
    """Subtitle/context (usually artist); None if unknown or useless."""
    return item_artist_name(item, cache)

def cache_artist_from_playback(group_id, favorite_id, cache=None):
    """After loading a favorite, cache artist name from now-playing metadata."""
    cache = cache if cache is not None else load_artist_cache()
    meta = get_playback_metadata(group_id)
    artist = (meta.get("currentItem", {}).get("track", {}).get("artist") or {}).get("name")
    if not artist:
        return cache

    by_fav = cache.setdefault("byFavoriteId", {})
    by_fav[str(favorite_id)] = artist

    container_id = (meta.get("container", {}).get("id") or {}).get("objectId") or ""
    if "spotify:" in container_id:
        container_id = container_id[container_id.index("spotify:"):]
    match = re.search(r"spotify:artistTopTracks:([A-Za-z0-9]+)", container_id)
    if match:
        cache.setdefault("bySpotifyArtistId", {})[match.group(1)] = artist

    save_artist_cache(cache)
    return cache

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
    """delta_steps: signed detent count (batched)."""
    pct = int(os.environ.get("VOLUME_PCT_PER_DETENT", "2"))
    _post(f"/groups/{group_id}/groupVolume/relative",
          {"volumeDelta": delta_steps * pct})

def get_favorites(household_id):
    return _get(f"/households/{household_id}/favorites")["items"]

def get_playback_metadata(group_id):
    return _get(f"/groups/{group_id}/playbackMetadata")

def load_favorite(group_id, favorite_id):
    resp = _post(f"/groups/{group_id}/favorites",
                 {"favoriteId": favorite_id, "playOnCompletion": True, "action": "REPLACE"})
    _check_response(resp)