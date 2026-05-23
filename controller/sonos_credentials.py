import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing {name}. Copy .env.example to .env and set your Sonos API credentials."
        )
    return value


CLIENT_ID = _require("SONOS_CLIENT_ID")
CLIENT_SECRET = _require("SONOS_CLIENT_SECRET")
