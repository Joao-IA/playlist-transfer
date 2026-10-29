import csv

import pytest

from music_transfer.engine import Transfer
from music_transfer.matching import SearchStep
from music_transfer.models import Candidate, Playlist, PlaylistRef, Track
from music_transfer.providers.base import Provider
from music_transfer.storage import Paths


class FakeSource(Provider):
    name = "src"
    label = "Source"
    needs_login = False


class FakeDest(Provider):
    """An in-memory streaming service that outlives Transfer instances, like a real one."""

    name = "dest"
    label = "Dest"
    needs_login = False
    batch_size = 2

    def __init__(self, paths, catalog):
        super().__init__(paths)
        self.catalog = catalog
        self.remote: dict[str, dict] = {}
        self.searches = 0
        self.add_calls = 0
        self.fail_on_add_call: int | None = None

    def search_steps(self, track):
        def run():
            self.searches += 1
            return [c for c in self.catalog if c.title == track.title]

        return [SearchStep("songs", run)]

    def list_playlists(self):
        return [PlaylistRef(i, p["name"]) for i, p in self.remote.items()]

    def create_playlist(self, name, description):
        playlist_id = f"pl{len(self.remote) + 1}"
        self.remote[playlist_id] = {"name": name, "items": []}
        return playlist_id

    def rename_playlist(self, playlist_id, name):
        self.remote[playlist_id]["name"] = name

    def playlist_item_ids(self, playlist_id):
        return set(self.remote[playlist_id]["items"])

    def add_items(self, playlist_id, item_ids):
        self.add_calls += 1
        if self.add_calls == self.fail_on_add_call:
            raise ConnectionError("network down")
        self.remote[playlist_id]["items"] += item_ids

    def item_url(self, item_id):
        return f"https://dest.example/{item_id}"

    def playlist_url(self, playlist_id):
        return f"https://dest.example/playlist/{playlist_id}"


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path)


@pytest.fixture
def dest(paths):
    # "Song 5" isn't on the destination
    return FakeDest(paths, [Candidate(f"id{i}", f"Song {i}", ["Band"]) for i in range(1, 5)])


@pytest.fixture
def playlist():
    tracks = [Track(f"src:{i}", f"Song {i}", ["Band"], position=i) for i in range(1, 6)]
    return Playlist("p1", "Road Trip", tracks, skipped=[Track("src:local", "My Demo", ["Me"], position=6)])


def run(paths, dest, playlist, **kwargs):
    name = kwargs.pop("name", None)
    Transfer(FakeSource(paths), dest, paths, **kwargs).run(playlist, name)


def test_creates_the_playlist_and_adds_the_matches(paths, dest, playlist):
    run(paths, dest, playlist)
    assert dest.remote == {"pl1": {"name": "Road Trip", "items": ["id1", "id2", "id3", "id4"]}}


def test_report_has_a_row_per_source_track(paths, dest, playlist):
    run(paths, dest, playlist)
    with (paths.output / "src-to-dest" / "Road Trip.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["status"] for r in rows] == ["matched"] * 4 + ["not_found", "skipped"]
    assert rows[0]["link"] == "https://dest.example/id1"


def test_running_again_changes_nothing_and_uses_the_cache(paths, dest, playlist):
    run(paths, dest, playlist)
    run(paths, dest, playlist)
    assert list(dest.remote) == ["pl1"]
    assert dest.remote["pl1"]["items"] == ["id1", "id2", "id3", "id4"]
    assert dest.searches == 5


def test_rematch_searches_again(paths, dest, playlist):
    run(paths, dest, playlist)
    run(paths, dest, playlist, rematch=True)
    assert dest.searches == 10


def test_resumes_after_a_failure_without_duplicates(paths, dest, playlist):
    dest.fail_on_add_call = 2
    with pytest.raises(ConnectionError):
        run(paths, dest, playlist)
    assert dest.remote["pl1"]["items"] == ["id1", "id2"]

    run(paths, dest, playlist)
    assert dest.remote["pl1"]["items"] == ["id1", "id2", "id3", "id4"]


def test_dry_run_changes_nothing(paths, dest, playlist):
    run(paths, dest, playlist, dry_run=True)
    assert dest.remote == {}
    assert (paths.output / "src-to-dest" / "Road Trip.csv").exists()


def test_name_renames_the_playlist_created_before(paths, dest, playlist):
    run(paths, dest, playlist)
    run(paths, dest, playlist, name="Summer Trip")
    assert dest.remote["pl1"]["name"] == "Summer Trip"
    run(paths, dest, playlist)  # the new name sticks without --name
    assert list(dest.remote) == ["pl1"]
    assert dest.remote["pl1"]["name"] == "Summer Trip"


def test_keeps_the_name_when_the_service_cant_rename(paths, dest, playlist, capsys):
    dest.can_rename = False
    run(paths, dest, playlist)
    run(paths, dest, playlist, name="Summer Trip")
    assert dest.remote["pl1"]["name"] == "Road Trip"
    assert "can't rename" in capsys.readouterr().out


def test_reuses_a_playlist_with_the_same_name(paths, dest, playlist):
    dest.remote["existing"] = {"name": "Road Trip", "items": ["id1"]}
    run(paths, dest, playlist)
    assert list(dest.remote) == ["existing"]
    assert dest.remote["existing"]["items"] == ["id1", "id2", "id3", "id4"]


def test_repeated_tracks_are_added_once(paths, dest, playlist):
    playlist.tracks.append(Track("src:1", "Song 1", ["Band"], position=7))
    run(paths, dest, playlist)
    assert dest.remote["pl1"]["items"] == ["id1", "id2", "id3", "id4"]
