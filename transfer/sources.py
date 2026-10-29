"""Leitura dos CSVs exportados pelo Exportify."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Track:
    position: int
    uri: str
    title: str
    artists: list[str]
    album: str
    duration_ms: int | None
    isrc: str

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else ""


@dataclass
class Playlist:
    name: str
    file: Path
    tracks: list[Track]
    skipped: list[Track]  # linhas sem URI de faixa (arquivos locais, faixas removidas do Spotify)


def playlist_name(path: Path) -> str:
    # O Exportify troca os espaços do nome da playlist por "_" no nome do arquivo
    return path.stem.replace("_", " ")


def load_playlist(path: Path) -> Playlist:
    tracks, skipped = [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for position, row in enumerate(csv.DictReader(f), start=1):
            duration = (row.get("Duration (ms)") or "").strip()
            track = Track(
                position=position,
                uri=(row.get("Track URI") or "").strip(),
                title=(row.get("Track Name") or "").strip(),
                # Artistas vêm separados por ";" ("Tyler, The Creator" tem vírgula no nome)
                artists=[a.strip() for a in (row.get("Artist Name(s)") or "").split(";") if a.strip()],
                album=(row.get("Album Name") or "").strip(),
                duration_ms=int(duration) if duration.isdigit() else None,
                isrc=(row.get("ISRC") or "").strip(),
            )
            (tracks if track.uri.startswith("spotify:track:") else skipped).append(track)
    return Playlist(playlist_name(path), path, tracks, skipped)


def load_playlists(directory: Path) -> list[Playlist]:
    return [load_playlist(path) for path in sorted(directory.glob("*.csv"))]
