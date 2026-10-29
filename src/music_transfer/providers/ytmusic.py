"""YouTube Music through ytmusicapi, logged in with request headers copied from the browser."""

import json
import re

from ytmusicapi import YTMusic, setup

from ..http import call
from ..matching import ACCEPT, ACCEPT_VIDEO, SearchStep, search_query
from ..models import Candidate, Playlist, PlaylistRef, Track
from ..storage import Paths, save_json
from .base import AuthError, Provider

DELAY = 0.7  # pause between calls so YouTube doesn't block us


def clean_headers(raw: str) -> str:
    """Remove the "Decoded:" block (x-client-data) that Chrome includes and ytmusicapi doesn't understand."""
    lines, skipping = [], False
    for line in raw.splitlines():
        if line.strip().lower() in ("decoded:", "decodificados:"):
            skipping = True
        elif skipping:
            skipping = line.strip() != "}"
        else:
            lines.append(line)
    return "\n".join(lines)


def _to_candidate(result: dict, kind: str) -> Candidate:
    seconds = result.get("duration_seconds")
    return Candidate(
        id=result.get("videoId") or "",
        title=result.get("title") or "",
        artists=[a["name"] for a in result.get("artists") or []],
        album=(result.get("album") or {}).get("name") or "",
        duration_ms=seconds * 1000 if seconds else None,
        kind=kind,
    )


def _to_track(item: dict, position: int) -> Track:
    c = _to_candidate(item, "song")
    return Track(f"ytmusic:{c.id}", c.title, c.artists, c.album, c.duration_ms, position=position)


def _count(value) -> int | None:
    # Library listings give the track count as text ("25")
    text = str(value or "").replace(",", "")
    return int(text) if text.isdigit() else None


class YouTubeMusic(Provider):
    name = "ytmusic"
    label = "YouTube Music"
    search_needs_login = False
    batch_size = 50

    def __init__(self, paths: Paths):
        super().__init__(paths)
        # Search works without login, so --dry-run doesn't need it
        self.yt = YTMusic(str(self.auth_file)) if self.logged_in else YTMusic()

    def _call(self, fn, *args, **kwargs):
        return call(fn, *args, delay=DELAY, **kwargs)

    @classmethod
    def login(cls, paths: Paths) -> None:
        headers_file = paths.auth / "ytmusic-headers.txt"
        if not headers_file.exists() or not headers_file.read_text("utf-8").strip():
            headers_file.parent.mkdir(parents=True, exist_ok=True)
            headers_file.touch()
            raise AuthError(
                f"Paste the request headers from music.youtube.com into {headers_file}, "
                "save it and run this again (see the README)."
            )
        auth_file = paths.auth / f"{cls.name}.json"
        try:
            headers = json.loads(setup(headers_raw=clean_headers(headers_file.read_text("utf-8"))))
            # Chrome's pseudo-headers (":path", ":authority") become invalid keys, and with
            # "content-encoding: gzip" YouTube expects a compressed body and rejects the request
            headers = {k: v for k, v in headers.items() if re.fullmatch(r"[a-z0-9-]+", k) and k != "content-encoding"}
            save_json(auth_file, headers, private=True)
            playlists = YTMusic(str(auth_file)).get_library_playlists(limit=None)
        except Exception as error:
            auth_file.unlink(missing_ok=True)
            raise AuthError(
                f"YouTube Music login failed: {error}\n"
                "Copy the headers again (a POST 'browse' request, while logged in) and retry."
            ) from error
        headers_file.unlink()  # it holds the session cookies; the .json keeps only what's needed
        print(f"Logged in to YouTube Music ({len(playlists)} playlists in your library).")

    def list_playlists(self) -> list[PlaylistRef]:
        self.require_login()
        playlists = self._call(self.yt.get_library_playlists, limit=None)
        return [PlaylistRef(p["playlistId"], p.get("title") or "", _count(p.get("count"))) for p in playlists]

    def empty_message(self) -> str:
        # An expired session isn't an error for YouTube: it just answers as if logged out
        return (
            "No playlists found on YouTube Music. If you do have playlists, the login has expired: "
            "run music-transfer login ytmusic"
        )

    def get_playlist(self, playlist_id: str) -> Playlist:
        self.require_login()
        data = self._call(self.yt.get_playlist, playlist_id, limit=None)
        tracks, skipped = [], []
        for position, item in enumerate(data.get("tracks") or [], start=1):
            # Items without a videoId were removed from YouTube
            (tracks if item.get("videoId") else skipped).append(_to_track(item, position))
        return Playlist(playlist_id, data.get("title") or playlist_id, tracks, skipped)

    def search_steps(self, track: Track) -> list[SearchStep]:
        query = search_query(track)
        steps = [SearchStep("songs", lambda: self._search(query, "songs", 5))]
        if track.isrc:
            # There's no ISRC lookup, but searching the code as text sometimes finds the song
            steps.append(SearchStep("isrc", lambda: self._search(track.isrc, "songs", 3)))
        # Plain videos only when no song was accepted, and they need a higher score
        steps.append(
            SearchStep("videos", lambda: self._search(query, "videos", 5), accept=ACCEPT_VIDEO, skip_if=ACCEPT)
        )
        return steps

    def _search(self, query: str, category: str, limit: int) -> list[Candidate]:
        kind = "video" if category == "videos" else "song"
        results = self._call(self.yt.search, query, filter=category, limit=limit)
        return [_to_candidate(r, kind) for r in results if r.get("videoId")]

    def create_playlist(self, name: str, description: str) -> str:
        playlist_id = self._call(self.yt.create_playlist, name, description, privacy_status="PRIVATE", retry=False)
        if not isinstance(playlist_id, str):
            raise RuntimeError(f"Could not create the playlist {name}: {playlist_id}")
        return playlist_id

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self._call(self.yt.edit_playlist, playlist_id, title=name)

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        data = self._call(self.yt.get_playlist, playlist_id, limit=None)
        return {t["videoId"] for t in data.get("tracks") or [] if t.get("videoId")}

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        result = self._call(self.yt.add_playlist_items, playlist_id, item_ids, duplicates=False)
        if not (isinstance(result, dict) and "SUCCEEDED" in str(result.get("status"))):
            raise RuntimeError(f"Could not add the songs (run again to continue): {result}")

    def item_url(self, item_id: str) -> str:
        return f"https://music.youtube.com/watch?v={item_id}"

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={playlist_id}"
