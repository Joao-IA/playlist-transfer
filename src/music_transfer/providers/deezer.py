"""Deezer: the public API (no login) for searching, the web app's gateway (arl cookie) for the rest.

Deezer doesn't register new API apps, so reading private playlists and writing go through
gw-light.php, the endpoint deezer.com itself uses. It's undocumented and may change."""

import getpass

from ..http import ApiClient, RetryableError
from ..matching import SearchStep, search_query
from ..models import Candidate, Playlist, PlaylistRef, Track
from ..storage import Paths, save_json
from .base import AuthError, Provider

PUBLIC_API = "https://api.deezer.com"
GATEWAY = "https://www.deezer.com/ajax/gw-light.php"
PRIVATE = 1  # playlist status: 0 public, 1 private, 2 collaborative
QUOTA_EXCEEDED = 4  # public API error code, sent with HTTP 200


def _check_public(data) -> None:
    error = data.get("error") if isinstance(data, dict) else None
    if error and error.get("code") == QUOTA_EXCEEDED:
        raise RetryableError("Deezer quota exceeded", wait=5)


def _public_to_candidate(item: dict) -> Candidate:
    # Full track objects list every artist in "contributors"; search results only have "artist"
    artists = [c.get("name") for c in item.get("contributors") or []] or [(item.get("artist") or {}).get("name")]
    return Candidate(
        id=str(item["id"]),
        title=item.get("title") or "",
        artists=[a for a in artists if a],
        album=(item.get("album") or {}).get("title") or "",
        duration_ms=item["duration"] * 1000 if item.get("duration") else None,
        isrc=item.get("isrc") or "",
    )


def _gw_to_track(song: dict, position: int) -> Track:
    title = song.get("SNG_TITLE") or ""
    if song.get("VERSION"):
        title = f"{title} {song['VERSION']}"  # e.g. "(Remastered 2011)"; normalize() drops the noise
    duration = str(song.get("DURATION") or "")
    artists = [a.get("ART_NAME") for a in song.get("ARTISTS") or []] or [song.get("ART_NAME")]
    return Track(
        id=f"deezer:{song.get('SNG_ID')}",
        title=title,
        artists=[a for a in artists if a],
        album=song.get("ALB_TITLE") or "",
        duration_ms=int(duration) * 1000 if duration.isdigit() else None,
        isrc=song.get("ISRC") or "",
        position=position,
    )


class Deezer(Provider):
    name = "deezer"
    label = "Deezer"
    search_needs_login = False

    def __init__(self, paths: Paths):
        super().__init__(paths)
        self.public = ApiClient(PUBLIC_API, delay=0.15, check=_check_public)  # quota: 50 requests per 5 s
        self.gateway = ApiClient(GATEWAY, delay=0.3)
        if self.logged_in:
            self.gateway.session.cookies.set("arl", self.credentials["arl"], domain=".deezer.com")
        self._session: dict | None = None  # api token and user id, fetched on first use

    @classmethod
    def login(cls, paths: Paths) -> None:
        arl = getpass.getpass("Value of the 'arl' cookie from deezer.com (input is hidden): ").strip()
        if not arl:
            raise AuthError("The arl cookie is required. See the Deezer section of the README.")
        auth_file = paths.auth / f"{cls.name}.json"
        save_json(auth_file, {"arl": arl}, private=True)
        try:
            user = cls(paths)._user()
        except Exception:
            auth_file.unlink(missing_ok=True)
            raise
        print(f"Logged in to Deezer as {user['name']}.")

    def _gateway_call(self, method: str, args: dict | None = None, token: str = "null", retry: bool = True):
        params = {"method": method, "input": "3", "api_version": "1.0", "api_token": token}
        data = self.gateway.post("", params=params, json=args or {}, retry=retry)
        if data.get("error"):
            raise RuntimeError(f"Deezer {method} failed: {data['error']}")
        return data["results"]

    def _user(self) -> dict:
        if self._session is None:
            self.require_login()
            results = self._gateway_call("deezer.getUserData")
            user = results.get("USER") or {}
            if not user.get("USER_ID"):
                raise AuthError("The Deezer arl cookie isn't valid anymore. Run: music-transfer login deezer")
            self._session = {"token": results["checkForm"], "id": user["USER_ID"], "name": user.get("BLOG_NAME")}
        return self._session

    def _gw(self, method: str, args: dict, retry: bool = True):
        return self._gateway_call(method, args, self._user()["token"], retry)

    def _songs(self, playlist_id: str) -> list[dict]:
        return self._gw("playlist.getSongs", {"PLAYLIST_ID": playlist_id, "nb": -1})["data"]

    def list_playlists(self) -> list[PlaylistRef]:
        page = self._gw("deezer.pageProfile", {"USER_ID": self._user()["id"], "tab": "playlists", "nb": 1000})
        return [
            PlaylistRef(str(p["PLAYLIST_ID"]), p.get("TITLE") or "", p.get("NB_SONG"))
            for p in page["TAB"]["playlists"]["data"]
        ]

    def get_playlist(self, playlist_id: str) -> Playlist:
        info = self._gw("deezer.pagePlaylist", {"PLAYLIST_ID": playlist_id, "lang": "en", "header": True, "tab": 0})
        tracks, skipped = [], []
        for position, song in enumerate(self._songs(playlist_id), start=1):
            # Negative ids are the user's own uploaded MP3s, which don't exist on other services
            (tracks if int(song.get("SNG_ID") or 0) > 0 else skipped).append(_gw_to_track(song, position))
        return Playlist(playlist_id, info["DATA"].get("TITLE") or playlist_id, tracks, skipped)

    def search_steps(self, track: Track) -> list[SearchStep]:
        steps = []
        if track.isrc:
            steps.append(SearchStep("isrc", lambda: self._by_isrc(track.isrc)))
        steps.append(SearchStep("songs", lambda: self._search(search_query(track))))
        return steps

    def _by_isrc(self, isrc: str) -> list[Candidate]:
        data = self.public.get(f"/track/isrc:{isrc}")
        return [] if data.get("error") else [_public_to_candidate(data)]

    def _search(self, query: str) -> list[Candidate]:
        data = self.public.get("/search", params={"q": query, "limit": 10})
        return [_public_to_candidate(item) for item in data.get("data") or []]

    def create_playlist(self, name: str, description: str) -> str:
        args = {"title": name, "description": description, "status": PRIVATE, "songs": []}
        return str(self._gw("playlist.create", args, retry=False))

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self._gw("playlist.update", {"playlist_id": playlist_id, "title": name})

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        return {str(song["SNG_ID"]) for song in self._songs(playlist_id)}

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        songs = [[int(i), 0] for i in item_ids]  # [song id, position flag], as the web app sends them
        self._gw("playlist.addSongs", {"playlist_id": playlist_id, "songs": songs, "offset": -1})

    def item_url(self, item_id: str) -> str:
        return f"https://www.deezer.com/track/{item_id}"

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://www.deezer.com/playlist/{playlist_id}"
