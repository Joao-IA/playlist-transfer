"""Spotify playlists exported as CSV files by Exportify (https://exportify.app). Read-only."""

import csv
from pathlib import Path

from ..models import Playlist, PlaylistRef, Track
from .base import Provider


def playlist_name(path: Path) -> str:
    # Exportify replaces the spaces in the playlist name with "_" in the file name
    return path.stem.replace("_", " ")


def load_playlist(path: Path) -> Playlist:
    tracks, skipped = [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for position, row in enumerate(csv.DictReader(f), start=1):
            duration = (row.get("Duration (ms)") or "").strip()
            track = Track(
                id=(row.get("Track URI") or "").strip(),
                title=(row.get("Track Name") or "").strip(),
                # Artists are separated by ";" ("Tyler, The Creator" has a comma in the name)
                artists=[a.strip() for a in (row.get("Artist Name(s)") or "").split(";") if a.strip()],
                album=(row.get("Album Name") or "").strip(),
                duration_ms=int(duration) if duration.isdigit() else None,
                isrc=(row.get("ISRC") or "").strip(),
                position=position,
            )
            # Rows without a track URI are local files or tracks removed from Spotify
            (tracks if track.id.startswith("spotify:track:") else skipped).append(track)
    return Playlist(path.name, playlist_name(path), tracks, skipped)


class ExportifyCsv(Provider):
    name = "csv"
    label = "Exportify CSV"
    needs_login = False
    can_write = False

    @property
    def folder(self) -> Path:
        return self.paths.exports

    def list_playlists(self) -> list[PlaylistRef]:
        playlists = [load_playlist(path) for path in sorted(self.folder.glob("*.csv"))]
        return [PlaylistRef(p.id, p.name, len(p.tracks)) for p in playlists]

    def get_playlist(self, playlist_id: str) -> Playlist:
        return load_playlist(self.folder / playlist_id)

    def empty_message(self) -> str:
        return (
            f"No CSV files in {self.folder}. Export your playlists with Exportify (https://exportify.app) "
            "and put the files there, or point to another folder with --csv-dir."
        )
