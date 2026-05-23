# auth_setup.py — run this once on your Mac
import os, webbrowser, json, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests, base64, secrets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "controller"))
from sonos_credentials import CLIENT_ID, CLIENT_SECRET

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "controller", "tokens.json")
REDIRECT_URI  = "http://localhost:8888/callback"
SCOPE         = "playback-control-all"
STATE         = secrets.token_hex(16)

auth_url = (
    f"https://api.sonos.com/login/v3/oauth"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}"
    f"&state={STATE}"
)

code_holder = {}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        code_holder["code"] = params["code"][0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auth complete. You can close this tab.")

if __name__ == "__main__":
    webbrowser.open(auth_url)
    HTTPServer(("localhost", 8888), Handler).handle_request()

    credentials = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()

    resp = requests.post(
        "https://api.sonos.com/login/v3/oauth/access",
        headers={"Authorization": f"Basic {credentials}"},
        data={
            "grant_type":   "authorization_code",
            "code":         code_holder["code"],
            "redirect_uri": REDIRECT_URI,
        }
    )

    tokens = resp.json()
    if "access_token" not in tokens:
        err = tokens.get("error", "unknown")
        desc = tokens.get("error_description", resp.text)
        raise SystemExit(f"Auth failed ({resp.status_code}): {err} — {desc}")

    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"Done — {TOKEN_FILE} written")