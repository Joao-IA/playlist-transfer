"""Apple Music with the tokens the music.apple.com web player uses.

The official API needs a paid Apple Developer membership to sign a developer token. The web
player's token plus your Media-User-Token reach the same endpoints (same paths as the documented
api.music.apple.com), served from the web player's host."""

import getpass
import re
from collections.abc import Iterator

import requests

from ..http import ApiClient
from ..matching import SearchStep, search_query
from ..models import Candidate, Playlist, PlaylistRef, Track
from ..storage import Paths, save_json
from .base import AuthError, Provider

API = "https://amp-api.music.apple.com"
ORIGIN = "https://music.apple.com"
CATALOG_BATCH = 300  # maximum ids per catalog request


def _artists(attributes: dict) -> list[str]:
    # Apple gives one combined string ("Tyler, The Creator & Daniel Caesar"); the scorer handles it
    return [attributes["artistName"]] if attributes.get("artistName") else []


def _to_candidate(song: dict) -> Candidate:
    a = song.get("attributes") or {}
    return Candidate(
        id=song["id"],
        title=a.get("name") or "",
        artists=_artists(a),
        album=a.get("albumName") or "",
        duration_ms=a.get("durationInMillis"),
        isrc=a.get("isrc") or "",
    )


def _catalog_id(library_song: dict) -> str:
    play_params = (library_song.get("attributes") or {}).get("playParams") or {}
    return str(play_params.get("catalogId") or "")


class AppleMusic(Provider):
    name = "applemusic"
    label = "Apple Music"
    can_rename = False  # the API can't rename library playlists

    def __init__(self, paths: Paths):
        super().__init__(paths)
        self.api = ApiClient(API, headers={"Origin": ORIGIN}, delay=0.2)
        if self.logged_in:
            self.api.session.headers.update(
                {
                    "Authorization": f"Bearer {self.credentials['developer_token']}",
                    "Media-User-Token": self.credentials["user_token"],
                }
            )
        self.storefront = self.credentials.get("storefront", "us")

    @classmethod
    def login(cls, paths: Paths) -> None:
        print("Copy these from a request to amp-api.music.apple.com in DevTools (see the README).")
        authorization = getpass.getpass("authorization header (input is hidden): ").strip()
        developer_token = re.sub(r"^bearer\s+", "", authorization, flags=re.IGNORECASE)
        user_token = getpass.getpass("media-user-token header (input is hidden): ").strip()
        if not developer_token or not user_token:
            raise AuthError("Both tokens are required. See the Apple Music section of the README.")
        credentials = {"developer_token": developer_token, "user_token": user_token}
        auth_file = paths.auth / f"{cls.name}.json"
        save_json(auth_file, credentials, private=True)
        try:
            storefront = cls(paths).api.get("/v1/me/storefront")["data"][0]["id"]
        except Exception as error:
            auth_file.unlink(missing_ok=True)
            raise AuthError(f"Apple Music login failed: {error}") from error
        save_json(auth_file, {**credentials, "storefront": storefront}, private=True)
        print(f"Logged in to Apple Music (storefront: {storefront}).")

    def _get(self, path: str, **params):
        self.require_login()
        return self.api.get(path, params=params)

    def _pages(self, path: str) -> Iterator[dict]:
        page = self._get(path, limit=100)
        while page:
            yield from page.get("data") or []
            page = self._get(page["next"], limit=100) if page.get("next") else None

    def _library_songs(self, playlist_id: str) -> list[dict]:
        try:
            return list(self._pages(f"/v1/me/library/playlists/{playlist_id}/tracks"))
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                return []  # empty playlists answer 404
            raise

    def _isrcs(self, catalog_ids: list[str]) -> dict[str, str]:
        """Library songs don't carry the ISRC, so it comes from the catalog."""
        ids = [i for i in catalog_ids if i]
        isrcs = {}
        for start in range(0, len(ids), CATALOG_BATCH):
            batch = ",".join(ids[start : start + CATALOG_BATCH])
            for song in self._get(f"/v1/catalog/{self.storefront}/songs", ids=batch).get("data") or []:
                isrcs[song["id"]] = (song.get("attributes") or {}).get("isrc") or ""
        return isrcs

    def list_playlists(self) -> list[PlaylistRef]:
        return [
            PlaylistRef(p["id"], (p.get("attributes") or {}).get("name") or "")
            for p in self._pages("/v1/me/library/playlists")
        ]

    def get_playlist(self, playlist_id: str) -> Playlist:
        info = self._get(f"/v1/me/library/playlists/{playlist_id}")["data"][0]
        songs = self._library_songs(playlist_id)
        isrcs = self._isrcs([_catalog_id(s) for s in songs])
        tracks, skipped = [], []
        for position, song in enumerate(songs, start=1):
            a = song.get("attributes") or {}
            catalog_id = _catalog_id(song)
            track = Track(
                id=f"applemusic:{catalog_id or song['id']}",
                title=a.get("name") or "",
                artists=_artists(a),
                album=a.get("albumName") or "",
                duration_ms=a.get("durationInMillis"),
                isrc=isrcs.get(catalog_id, ""),
                position=position,
            )
            # Songs without a catalog id are the user's own uploads
            (tracks if catalog_id else skipped).append(track)
        return Playlist(playlist_id, (info.get("attributes") or {}).get("name") or playlist_id, tracks, skipped)

    def search_steps(self, track: Track) -> list[SearchStep]:
        steps = []
        if track.isrc:
            steps.append(SearchStep("isrc", lambda: self._by_isrc(track.isrc)))
        steps.append(SearchStep("songs", lambda: self._search(search_query(track))))
        return steps

    def _by_isrc(self, isrc: str) -> list[Candidate]:
        data = self._get(f"/v1/catalog/{self.storefront}/songs", **{"filter[isrc]": isrc})
        return [_to_candidate(song) for song in data.get("data") or []]

    def _search(self, query: str) -> list[Candidate]:
        data = self._get(f"/v1/catalog/{self.storefront}/search", term=query, types="songs", limit=10)
        return [_to_candidate(song) for song in ((data.get("results") or {}).get("songs") or {}).get("data") or []]

    def create_playlist(self, name: str, description: str) -> str:
        self.require_login()
        body = {"attributes": {"name": name, "description": description}}
        return self.api.post("/v1/me/library/playlists", json=body, retry=False)["data"][0]["id"]

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        return {i for i in map(_catalog_id, self._library_songs(playlist_id)) if i}

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        self.require_login()
        body = {"data": [{"id": i, "type": "songs"} for i in item_ids]}
        self.api.post(f"/v1/me/library/playlists/{playlist_id}/tracks", json=body)

    def item_url(self, item_id: str) -> str:
        return f"https://music.apple.com/{self.storefront}/song/{item_id}"

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://music.apple.com/library/playlist/{playlist_id}"
