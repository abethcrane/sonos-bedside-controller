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
