"""CSV report of a transfer: one row per source track with what it matched on the destination."""

import csv
import re
from collections.abc import Callable
from pathlib import Path

from .matching import Match
from .models import Playlist

HEADER = [
    "position",
    "status",
    "score",
    "source_title",
    "source_artists",
    "match_title",
    "match_artists",
    "matched_by",
    "link",
]


def report_path(directory: Path, playlist: Playlist) -> Path:
    # Playlist names can contain characters that aren't allowed in file names
    stem = re.sub(r'[\\/:*?"<>|]+', "_", playlist.name).strip(" .") or playlist.id
    return directory / f"{stem}.csv"


def write_report(directory: Path, playlist: Playlist, matches: list[Match], item_url: Callable[[str], str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = report_path(directory, playlist)
    rows = list(zip(playlist.tracks, matches, strict=True)) + [(t, None) for t in playlist.skipped]
    rows.sort(key=lambda row: row[0].position)
    # utf-8-sig so Excel shows accented titles correctly
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for track, match in rows:
            artists = ", ".join(track.artists)
            if match is None:
                writer.writerow([track.position, "skipped", "", track.title, artists, "", "", "", ""])
                continue
            link = item_url(match.item_id) if match.item_id else ""
            writer.writerow(
                [track.position, match.status, match.score, track.title, artists,
                 match.title, match.artists, match.source, link]
            )  # fmt: skip
    return path
