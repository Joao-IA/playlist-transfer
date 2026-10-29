"""SoundCloud through api-v2, the API soundcloud.com itself uses, with your browser session.

Registering an app for the official API needs an Artist Pro subscription; the web app's
client_id and your oauth_token cookie reach the same data. Many label releases aren't on
SoundCloud, so expect more "not_found" than on other services."""

import getpass
from collections.abc import Iterator
from urllib.parse import quote

from ..http import ApiClient
from ..matching import SearchStep, search_query
from ..models import Candidate, Playlist, PlaylistRef, Track
from ..storage import Paths, save_json
from .base import AuthError, Provider

API = "https://api-v2.soundcloud.com"
MAX_PLAYLIST = 500  # SoundCloud's limit of tracks per playlist
TRACKS_BATCH = 50  # maximum ids per /tracks request


def _to_candidate(track: dict) -> Candidate:
    meta = track.get("publisher_metadata") or {}
    artist = meta.get("artist") or (track.get("user") or {}).get("username") or ""
    return Candidate(
        id=str(track["id"]),
        title=track.get("title") or "",
        artists=[artist] if artist else [],
        album=meta.get("album_title") or "",
        # "duration" is only the 30 s preview for Go+ tracks
        duration_ms=track.get("full_duration") or track.get("duration"),
        isrc=meta.get("isrc") or "",
    )


def _to_track(track: dict, position: int) -> Track:
    c = _to_candidate(track)
    return Track(f"soundcloud:{c.id}", c.title, c.artists, c.album, c.duration_ms, c.isrc, position)


class SoundCloud(Provider):
    name = "soundcloud"
    label = "SoundCloud"

    def __init__(self, paths: Paths):
        super().__init__(paths)
        self.api = ApiClient(API, delay=0.3)
        if self.logged_in:
            self.api.session.headers["Authorization"] = f"OAuth {self.credentials['oauth_token']}"
            self.api.params["client_id"] = self.credentials["client_id"]

    @classmethod
    def login(cls, paths: Paths) -> None:
        print("Copy these from soundcloud.com while logged in (see the README).")
        client_id = input("client_id (from the URL of any api-v2.soundcloud.com request): ").strip()
        oauth_token = getpass.getpass("oauth_token cookie (input is hidden): ").strip()
        oauth_token = oauth_token.removeprefix("OAuth ").strip()
        if not client_id or not oauth_token:
            raise AuthError("Both values are required. See the SoundCloud section of the README.")
        credentials = {"client_id": client_id, "oauth_token": oauth_token}
        auth_file = paths.auth / f"{cls.name}.json"
        save_json(auth_file, credentials, private=True)
        try:
            me = cls(paths)._get("/me")
        except Exception as error:
            auth_file.unlink(missing_ok=True)
            raise AuthError(f"SoundCloud login failed: {error}") from error
        save_json(auth_file, {**credentials, "user_id": me["id"]}, private=True)
        print(f"Logged in to SoundCloud as {me.get('username')}.")

    def _get(self, path: str, **params):
        self.require_login()
        return self.api.get(path, params=params)

    def _pages(self, path: str) -> Iterator[dict]:
        page = self._get(path, limit=50, linked_partitioning=1)
        while page:
            yield from page.get("collection") or []
            page = self._get(page["next_href"]) if page.get("next_href") else None

    def _full_tracks(self, ids: list[str]) -> dict[str, dict]:
        """Playlists only carry full data for their first few tracks; the rest come from /tracks."""
        tracks = {}
        for start in range(0, len(ids), TRACKS_BATCH):
            for track in self._get("/tracks", ids=",".join(ids[start : start + TRACKS_BATCH])) or []:
                tracks[str(track["id"])] = track
        return tracks

    def _track_ids(self, playlist_id: str) -> list[int]:
        return [t["id"] for t in self._get(f"/playlists/{playlist_id}").get("tracks") or []]

    def list_playlists(self) -> list[PlaylistRef]:
        user_id = self.require_login()["user_id"]
        return [
            PlaylistRef(str(p["id"]), p.get("title") or "", p.get("track_count"))
            for p in self._pages(f"/users/{user_id}/playlists_without_albums")
        ]

    def get_playlist(self, playlist_id: str) -> Playlist:
        playlist = self._get(f"/playlists/{playlist_id}")
        ids = [str(t["id"]) for t in playlist.get("tracks") or []]
        full = self._full_tracks(ids)
        tracks, skipped = [], []
        for position, track_id in enumerate(ids, start=1):
            if track_id in full:
                tracks.append(_to_track(full[track_id], position))
            else:  # removed, or made private by the uploader
                skipped.append(Track(f"soundcloud:{track_id}", "", [], position=position))
        return Playlist(playlist_id, playlist.get("title") or playlist_id, tracks, skipped)

    def search_steps(self, track: Track) -> list[SearchStep]:
        return [SearchStep("songs", lambda: self._search(search_query(track)))]

    def _search(self, query: str) -> list[Candidate]:
        data = self._get("/search/tracks", q=query, limit=10)
        return [_to_candidate(track) for track in data.get("collection") or []]

    def create_playlist(self, name: str, description: str) -> str:
        self.require_login()
        body = {"playlist": {"title": name, "description": description, "sharing": "private", "tracks": []}}
        return str(self.api.post("/playlists", json=body, retry=False)["id"])

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self.require_login()
        self.api.put(f"/playlists/{playlist_id}", json={"playlist": {"title": name}})

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        return {str(i) for i in self._track_ids(playlist_id)}

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        # The API replaces the whole track list, so send the current tracks plus the new ones
        tracks = self._track_ids(playlist_id) + [int(i) for i in item_ids]
        if len(tracks) > MAX_PLAYLIST:
            raise RuntimeError(f"SoundCloud playlists hold at most {MAX_PLAYLIST} tracks")
        self.api.put(f"/playlists/{playlist_id}", json={"playlist": {"tracks": tracks}})

    def item_url(self, item_id: str) -> str:
        # Track pages are addressed by permalink, which search results don't keep; the widget takes the id
        return f"https://w.soundcloud.com/player/?url={quote(f'https://api.soundcloud.com/tracks/{item_id}', safe='')}"

    def playlist_url(self, playlist_id: str) -> str:
        return self._get(f"/playlists/{playlist_id}").get("permalink_url") or playlist_id
