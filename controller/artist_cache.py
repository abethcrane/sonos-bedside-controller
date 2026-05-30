import json
import os

CACHE_FILE = os.path.join(os.path.dirname(__file__), "artist_cache.json")


def load_artist_cache():
    if not os.path.exists(CACHE_FILE):
        return {"bySpotifyArtistId": {}, "byFavoriteId": {}}
    with open(CACHE_FILE) as f:
        data = json.load(f)
    data.setdefault("bySpotifyArtistId", {})
    data.setdefault("byFavoriteId", {})
    return data


def save_artist_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
