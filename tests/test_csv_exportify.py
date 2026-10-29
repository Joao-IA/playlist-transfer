import csv

import pytest

from music_transfer.providers.csv_exportify import ExportifyCsv
from music_transfer.storage import Paths

HEADER = ["Track URI", "ISRC", "Track Name", "Album Name", "Artist Name(s)", "Duration (ms)"]


@pytest.fixture
def provider(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    # Exportify writes UTF-8 with a BOM
    with (exports / "Road_Trip.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerow(
            ["spotify:track:1", "USQX92405783", "St. Chroma (feat. Daniel Caesar)", "CHROMAKOPIA",
             "Tyler, The Creator;Daniel Caesar", "197019"]
        )  # fmt: skip
        writer.writerow(["spotify:local:Me::My+Demo:180", "", "My Demo", "", "Me", "180000"])
        writer.writerow(["", "", "Removed Song", "", "Someone", ""])
    return ExportifyCsv(Paths(tmp_path))


def test_lists_one_playlist_per_file(provider):
    [ref] = provider.list_playlists()
    assert (ref.id, ref.name, ref.track_count) == ("Road_Trip.csv", "Road Trip", 1)


def test_reads_tracks(provider):
    playlist = provider.get_playlist("Road_Trip.csv")
    [track] = playlist.tracks
    assert track.id == "spotify:track:1"
    assert track.title == "St. Chroma (feat. Daniel Caesar)"
    assert track.artists == ["Tyler, The Creator", "Daniel Caesar"]  # split on ";", not ","
    assert (track.album, track.duration_ms, track.isrc, track.position) == ("CHROMAKOPIA", 197019, "USQX92405783", 1)


def test_local_files_and_removed_tracks_are_skipped(provider):
    playlist = provider.get_playlist("Road_Trip.csv")
    assert [(t.title, t.position) for t in playlist.skipped] == [("My Demo", 2), ("Removed Song", 3)]


def test_empty_folder(tmp_path):
    provider = ExportifyCsv(Paths(tmp_path))
    assert provider.list_playlists() == []
    assert "exports" in provider.empty_message()


def test_csv_dir_overrides_exports(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "Mix.csv").write_text(",".join(HEADER) + "\n", encoding="utf-8")
    assert [r.name for r in ExportifyCsv(Paths(tmp_path, csv_dir=other)).list_playlists()] == ["Mix"]
