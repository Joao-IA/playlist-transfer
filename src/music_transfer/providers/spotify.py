"""Spotify through the official Web API, with OAuth + PKCE (no client secret needed).

Uses the endpoints from the February 2026 Web API update (/me/playlists, /playlists/{id}/items).
Development-mode apps need the app owner to have Spotify Premium and allow 5 users."""

import base64
import hashlib
import os
import secrets
import time
import webbrowser
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..http import TIMEOUT, ApiClient
from ..matching import SearchStep, search_query
from ..models import Candidate, Playlist, PlaylistRef, Track
from ..storage import Paths, save_json
from .base import AuthError, Provider

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"  # Spotify accepts loopback IPs only, not "localhost"
SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"
SEARCH_LIMIT = 10  # maximum since February 2026


def _to_candidate(item: dict) -> Candidate:
    return Candidate(
        id=item.get("id") or "",
        title=item.get("name") or "",
        artists=[a["name"] for a in item.get("artists") or []],
        album=(item.get("album") or {}).get("name") or "",
        duration_ms=item.get("duration_ms"),
        isrc=(item.get("external_ids") or {}).get("isrc") or "",
    )


def _to_track(item: dict, position: int) -> Track:
    c = _to_candidate(item)
    return Track(item.get("uri") or "", c.title, c.artists, c.album, c.duration_ms, c.isrc, position)


def _entry_item(entry: dict) -> dict:
    # Playlist entries hold the track under "item" (it was "track" before February 2026)
    return entry.get("item") or entry.get("track") or {}


def _token_request(data: dict) -> dict:
    response = requests.post(TOKEN_URL, data=data, timeout=TIMEOUT)
    if not response.ok:
        raise AuthError(
            f"Spotify login failed ({response.status_code}): {response.text[:300]}\nRun: music-transfer login spotify"
        )
    token = response.json()
    result = {"access_token": token["access_token"], "expires_at": time.time() + token["expires_in"]}
    if token.get("refresh_token"):
        result["refresh_token"] = token["refresh_token"]
    return result


def _wait_for_code(state: str) -> str:
    """Run a one-shot local web server that receives the redirect from Spotify's login page."""
    query: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/callback":
                query.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done! You can close this tab and go back to the terminal.")

        def log_message(self, *args):  # keep the terminal clean
            pass

    redirect = urlparse(REDIRECT_URI)
    with HTTPServer((redirect.hostname, redirect.port), Handler) as server:
        server.timeout = 1  # wake up every second so Ctrl+C works
        while "code" not in query and "error" not in query:
            server.handle_request()
    if "error" in query:
        raise AuthError(f"Spotify login was not authorized: {query['error']}")
    if query.get("state") != state:
        raise AuthError("Spotify login failed (state mismatch). Try again.")
    return query["code"]


class Spotify(Provider):
    name = "spotify"
    label = "Spotify"
    batch_size = 100  # maximum per request

    def __init__(self, paths: Paths):
        super().__init__(paths)
        self.api = ApiClient("https://api.spotify.com/v1", delay=0.1)

    @classmethod
    def login(cls, paths: Paths) -> None:
        client_id = os.environ.get("SPOTIFY_CLIENT_ID") or input("Client ID of your Spotify app: ").strip()
        if not client_id:
            raise AuthError("A Client ID is required. See the Spotify section of the README.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        url = f"{AUTHORIZE_URL}?" + urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "scope": SCOPES,
                "state": state,
            }
        )
        print(f"Opening the browser to authorize. If it doesn't open, visit:\n{url}")
        webbrowser.open(url)
        code = _wait_for_code(state)
        token = _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": verifier,
            }
        )
        save_json(paths.auth / f"{cls.name}.json", {"client_id": client_id, **token}, private=True)
        user = cls(paths)._get("/me")
        print(f"Logged in to Spotify as {user.get('display_name') or user.get('id')}.")

    def _access_token(self) -> str:
        credentials = self.require_login()
        if time.time() > credentials["expires_at"] - 60:
            refresh = {
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
                "client_id": credentials["client_id"],
            }
            credentials.update(_token_request(refresh))
            # With PKCE the refresh token rotates, so the new one has to be saved
            save_json(self.auth_file, credentials, private=True)
        return credentials["access_token"]

    def _request(self, method: str, path: str, **kwargs):
        self.api.session.headers["Authorization"] = f"Bearer {self._access_token()}"
        return self.api.request(method, path, **kwargs)

    def _get(self, path: str, **kwargs):
        return self._request("GET", path, **kwargs)

    def _pages(self, path: str, **params) -> Iterator[dict]:
        page = self._get(path, params=params)
        while page:
            yield from page.get("items") or []
            page = self._get(page["next"]) if page.get("next") else None

    def list_playlists(self) -> list[PlaylistRef]:
        return [
            PlaylistRef(p["id"], p.get("name") or "", (p.get("items") or p.get("tracks") or {}).get("total"))
            for p in self._pages("/me/playlists", limit=50)
            if p
        ]

    def get_playlist(self, playlist_id: str) -> Playlist:
        name = self._get(f"/playlists/{playlist_id}", params={"fields": "name"}).get("name") or playlist_id
        tracks, skipped = [], []
        for position, entry in enumerate(self._pages(f"/playlists/{playlist_id}/items", limit=50), start=1):
            item = _entry_item(entry)
            track = _to_track(item, position)
            # Local files and podcast episodes don't exist on other services
            transferable = item.get("type") == "track" and not item.get("is_local") and track.id
            (tracks if transferable else skipped).append(track)
        return Playlist(playlist_id, name, tracks, skipped)

    def search_steps(self, track: Track) -> list[SearchStep]:
        steps = []
        if track.isrc:
            steps.append(SearchStep("isrc", lambda: self._search(f"isrc:{track.isrc}")))
        steps.append(SearchStep("songs", lambda: self._search(search_query(track))))
        return steps

    def _search(self, query: str) -> list[Candidate]:
        data = self._get("/search", params={"q": query, "type": "track", "limit": SEARCH_LIMIT})
        return [_to_candidate(item) for item in (data.get("tracks") or {}).get("items") or [] if item]

    def create_playlist(self, name: str, description: str) -> str:
        body = {"name": name, "description": description, "public": False}
        return self._request("POST", "/me/playlists", json=body, retry=False)["id"]

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self._request("PUT", f"/playlists/{playlist_id}", json={"name": name})

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        ids = {_entry_item(e).get("id") for e in self._pages(f"/playlists/{playlist_id}/items", limit=50)}
        return {i for i in ids if i}

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        uris = [f"spotify:track:{i}" for i in item_ids]
        self._request("POST", f"/playlists/{playlist_id}/items", json={"uris": uris})

    def item_url(self, item_id: str) -> str:
        return f"https://open.spotify.com/track/{item_id}"

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://open.spotify.com/playlist/{playlist_id}"
