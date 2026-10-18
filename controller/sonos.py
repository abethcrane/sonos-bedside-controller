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

# Sonos often puts the *service* in `description` for albums/playlists — not the artist.
_SERVICE_DESCRIPTIONS = frozenset({
    "spotify",
    "spotify playlist",
    "spotify album",
    "apple music",
    "amazon music",
    "amazon music unlimited",
    "tidal",
    "deezer",
    "pandora",
    "youtube music",
    "soundcloud",
    "tunein",
    "pocket casts",
})

def is_useless_description(desc):
    d = (desc or "").strip().lower()
    if not d or d == "from music library":
        return True
    if d in _SERVICE_DESCRIPTIONS:
        return True
    if d.startswith("spotify ") or d.startswith("apple music"):
        return True
    if d.endswith(" playlist"):
        return True
    return False

def _spotify_object_id(item):
    resource = item.get("resource") or {}
    obj_id = (resource.get("id") or {}).get("objectId") or ""
    if "spotify:" in obj_id:
        return obj_id[obj_id.index("spotify:"):]
    return obj_id

def spotify_artist_id_from_item(item):
    obj_id = _spotify_object_id(item)
    match = re.search(
        r"spotify:artistTopTracks:([A-Za-z0-9]+)|spotify:artist:([A-Za-z0-9]+)",
        obj_id,
    )
    if not match:
        return None
    return match.group(1) or match.group(2)

def spotify_album_id_from_item(item):
    obj_id = _spotify_object_id(item)
    match = re.search(r"spotify:album:([A-Za-z0-9]+)", obj_id)
    return match.group(1) if match else None

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

    album_id = spotify_album_id_from_item(item)
    if album_id:
        cached = cache.get("bySpotifyAlbumId", {}).get(album_id)
        if cached:
            return cached

    desc = (item.get("description") or "").strip()
    if desc and not is_useless_description(desc):
        return desc
    return None

def resolve_favorite_artists(favorites, cache=None):
    """Fill cache from Spotify artist/album IDs embedded in Sonos favorites."""
    from spotify import get_album_artists, get_artists, spotify_configured

    cache = cache if cache is not None else load_artist_cache()
    by_spotify = cache.setdefault("bySpotifyArtistId", {})
    by_album = cache.setdefault("bySpotifyAlbumId", {})
    by_fav = cache.setdefault("byFavoriteId", {})
    missing_artists = set()
    missing_albums = set()

    for fav in favorites:
        fav_id = str(fav.get("id", ""))
        if fav_id and fav_id in by_fav:
            continue
        artist_id = spotify_artist_id_from_item(fav)
        if artist_id and artist_id not in by_spotify:
            missing_artists.add(artist_id)
        album_id = spotify_album_id_from_item(fav)
        if album_id and album_id not in by_album:
            missing_albums.add(album_id)

    if spotify_configured():
        if missing_artists:
            by_spotify.update(get_artists(sorted(missing_artists)))
        if missing_albums:
            by_album.update(get_album_artists(sorted(missing_albums)))
        # Stash onto favorites so display works even if album cache is consulted later
        for fav in favorites:
            fav_id = str(fav.get("id", ""))
            if not fav_id or fav_id in by_fav:
                continue
            album_id = spotify_album_id_from_item(fav)
            if album_id and album_id in by_album:
                by_fav[fav_id] = by_album[album_id]
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
    album_match = re.search(r"spotify:album:([A-Za-z0-9]+)", container_id)
    if album_match:
        cache.setdefault("bySpotifyAlbumId", {})[album_match.group(1)] = artist

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

def get_groups(household_id):
    """Full groups response: {groups, players, …}."""
    return _get(f"/households/{household_id}/groups")

def get_players(household_id):
    """Discovered players: [{id, name, …}, …]."""
    return get_groups(household_id).get("players", [])

def resolve_player_ids(household_id, room_names):
    """
    Map room names (or raw player IDs) → player IDs.
    Returns (player_ids, missing_names).
    """
    players = get_players(household_id)
    by_name = {}
    by_id = {}
    for p in players:
        pid = p.get("id") or ""
        name = (p.get("name") or "").strip()
        if pid:
            by_id[pid] = pid
        if name:
            by_name[name.lower()] = pid
    player_ids = []
    missing = []
    seen = set()
    for raw in room_names:
        key = (raw or "").strip()
        if not key:
            continue
        pid = by_id.get(key) or by_name.get(key.lower())
        if not pid:
            missing.append(key)
            continue
        if pid not in seen:
            seen.add(pid)
            player_ids.append(pid)
    return player_ids, missing

def create_group(household_id, player_ids, music_context_group_id=None):
    """
    Create (or enlarge) a group from player IDs.
    Returns new group id. Optionally keeps audio from music_context_group_id.
    """
    if not player_ids:
        raise SonosError(400, "ERROR_INVALID_PARAMETER", "playerIds required")
    body = {"playerIds": list(player_ids)}
    if music_context_group_id:
        body["musicContextGroupId"] = music_context_group_id
    resp = _post(f"/households/{household_id}/groups/createGroup", body)
    _check_response(resp)
    data = resp.json()
    group = (data.get("group") or data.get("groupInfo", {}).get("group") or {})
    gid = group.get("id")
    if not gid:
        raise SonosError(502, "ERROR_UNKNOWN", f"createGroup missing group id: {data}")
    return gid

def modify_group_members(group_id, player_ids_to_add=None, player_ids_to_remove=None):
    body = {}
    if player_ids_to_add:
        body["playerIdsToAdd"] = list(player_ids_to_add)
    if player_ids_to_remove:
        body["playerIdsToRemove"] = list(player_ids_to_remove)
    resp = _post(f"/groups/{group_id}/groups/modifyGroupMembers", body)
    _check_response(resp)
    data = resp.json()
    group = (data.get("group") or data.get("groupInfo", {}).get("group") or {})
    return group.get("id") or group_id

def apply_room_preset(household_id, room_names, music_context_group_id=None):
    """
    Resolve room names → createGroup.
    Returns (group_id, missing_room_names).
    """
    player_ids, missing = resolve_player_ids(household_id, room_names)
    if not player_ids:
        raise SonosError(
            400,
            "ERROR_INVALID_PARAMETER",
            f"No matching players for rooms: {room_names}",
        )
    gid = create_group(household_id, player_ids, music_context_group_id)
    return gid, missing

def get_playlists(household_id):
    return _get(f"/households/{household_id}/playlists")["playlists"]

def load_playlist(group_id, playlist_id):
    resp = _post(f"/groups/{group_id}/playlists",
                 {"playlistId": playlist_id, "playOnCompletion": True, "action": "REPLACE"})
    _check_response(resp)

def play_pause(group_id):
    _post(f"/groups/{group_id}/playback/togglePlayPause")

def skip_to_next_track(group_id):
    resp = _post(f"/groups/{group_id}/playback/skipToNextTrack")
    _check_response(resp)

def skip_to_previous_track(group_id):
    resp = _post(f"/groups/{group_id}/playback/skipToPreviousTrack")
    _check_response(resp)

def get_group_volume(group_id):
    """(volume 0–100, muted bool) from groupVolume."""
    data = _get(f"/groups/{group_id}/groupVolume")
    return int(data.get("volume", 0)), bool(data.get("muted", False))

def get_volume(group_id):
    """Current group volume 0–100."""
    volume, _muted = get_group_volume(group_id)
    return volume

def set_volume_absolute(group_id, volume):
    """Set group volume to an absolute level 0–100 (also unmutes per Sonos API)."""
    level = max(0, min(100, int(volume)))
    _post(f"/groups/{group_id}/groupVolume", {"volume": level})

def set_mute(group_id, muted):
    """Mute / unmute the group without changing volume level."""
    resp = _post(f"/groups/{group_id}/groupVolume/mute", {"muted": bool(muted)})
    _check_response(resp)

def set_volume(group_id, delta_steps):
    """delta_steps: signed detent count → relative % (legacy / tests)."""
    pct = int(os.environ.get("VOLUME_PCT_PER_DETENT", "2"))
    _post(f"/groups/{group_id}/groupVolume/relative",
          {"volumeDelta": delta_steps * pct})

def get_favorites(household_id):
    return _get(f"/households/{household_id}/favorites")["items"]

def get_playback_metadata(group_id):
    return _get(f"/groups/{group_id}/playbackMetadata")

def _track_label(item):
    """Human label from a metadata item ({track: {name, artist}})."""
    if not item:
        return None
    track = item.get("track") or {}
    name = (track.get("name") or "").strip()
    artist = ((track.get("artist") or {}).get("name") or "").strip()
    if name and artist:
        return f"{name} · {artist}"
    return name or artist or None

def get_now_playing(group_id):
    """
    Current + next track labels from playbackMetadata.
    Sonos does not expose previous-track metadata here — only nextItem.
    Returns {current, next, container} (strings or None).
    """
    meta = get_playback_metadata(group_id)
    current = _track_label(meta.get("currentItem"))
    if not current:
        current = (meta.get("streamInfo") or "").strip() or None
    if not current:
        current = ((meta.get("container") or {}).get("name") or "").strip() or None
    nxt = _track_label(meta.get("nextItem"))
    container = ((meta.get("container") or {}).get("name") or "").strip() or None
    return {"current": current, "next": nxt, "container": container}

def load_favorite(group_id, favorite_id):
    resp = _post(f"/groups/{group_id}/favorites",
                 {"favoriteId": favorite_id, "playOnCompletion": True, "action": "REPLACE"})
    _check_response(resp)