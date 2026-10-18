import base64
import os
import time

import requests

REQUEST_TIMEOUT = 20
_token = None
_token_expires = 0.0


def spotify_configured():
    return bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))


def _client_credentials():
    global _token, _token_expires
    if _token and time.monotonic() < _token_expires:
        return _token

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expires = time.monotonic() + max(0, data.get("expires_in", 3600) - 60)
    return _token


def get_artists(artist_ids):
    """Return {spotifyArtistId: name} for up to 50 ids per call."""
    if not artist_ids:
        return {}
    token = _client_credentials()
    if not token:
        return {}

    out = {}
    for i in range(0, len(artist_ids), 50):
        batch = artist_ids[i : i + 50]
        resp = requests.get(
            "https://api.spotify.com/v1/artists",
            headers={"Authorization": f"Bearer {token}"},
            params={"ids": ",".join(batch)},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            continue
        for artist in resp.json().get("artists") or []:
            if artist and artist.get("id") and artist.get("name"):
                out[artist["id"]] = artist["name"]
    return out


def get_album_artists(album_ids):
    """Return {spotifyAlbumId: 'Artist' or 'A, B'} for up to 20 ids per call."""
    if not album_ids:
        return {}
    token = _client_credentials()
    if not token:
        return {}

    out = {}
    for i in range(0, len(album_ids), 20):
        batch = album_ids[i : i + 20]
        resp = requests.get(
            "https://api.spotify.com/v1/albums",
            headers={"Authorization": f"Bearer {token}"},
            params={"ids": ",".join(batch)},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            continue
        for album in resp.json().get("albums") or []:
            if not album or not album.get("id"):
                continue
            names = [
                a.get("name")
                for a in (album.get("artists") or [])
                if a and a.get("name")
            ]
            if names:
                out[album["id"]] = ", ".join(names)
    return out


def search_album_artist(album_name):
    """
    Best-effort album → artist via Spotify search.
    Used when Sonos favorites omit a spotify:album URI.
    """
    name = (album_name or "").strip()
    if not name:
        return None
    token = _client_credentials()
    if not token:
        return None
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f'album:"{name}"', "type": "album", "limit": 5},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    items = (resp.json().get("albums") or {}).get("items") or []
    if not items:
        # looser fallback
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": name, "type": "album", "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        items = (resp.json().get("albums") or {}).get("items") or []
    name_l = name.lower()
    # Prefer exact / case-insensitive title match
    ordered = sorted(
        items,
        key=lambda a: (
            0 if (a.get("name") or "").lower() == name_l else 1,
            0 if name_l in (a.get("name") or "").lower() else 1,
        ),
    )
    for album in ordered:
        names = [
            a.get("name")
            for a in (album.get("artists") or [])
            if a and a.get("name")
        ]
        if names:
            return ", ".join(names)
    return None
