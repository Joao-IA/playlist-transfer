"""The response -> model mappers of each service, on hand-written samples of their JSON."""

import pytest

from music_transfer.providers import PROVIDERS, apple_music, deezer, soundcloud, spotify, ytmusic
from music_transfer.providers.base import AuthError
from music_transfer.storage import Paths


class TestYouTubeMusic:
    def test_search_result(self):
        result = {
            "videoId": "abc123",
            "title": "Duality",
            "artists": [{"name": "Slipknot", "id": "UC1"}],
            "album": {"name": "Vol. 3", "id": "MPRE1"},
            "duration_seconds": 252,
        }
        c = ytmusic._to_candidate(result, "song")
        assert (c.id, c.title, c.artists, c.album, c.duration_ms, c.kind) == (
            "abc123", "Duality", ["Slipknot"], "Vol. 3", 252_000, "song"
        )  # fmt: skip

    def test_video_without_album(self):
        c = ytmusic._to_candidate({"videoId": "v", "title": "Duality", "artists": []}, "video")
        assert (c.album, c.duration_ms, c.kind) == ("", None, "video")

    def test_playlist_item_becomes_namespaced_track(self):
        track = ytmusic._to_track({"videoId": "abc123", "title": "Duality", "artists": [{"name": "Slipknot"}]}, 3)
        assert (track.id, track.position) == ("ytmusic:abc123", 3)

    def test_clean_headers_drops_chromes_decoded_block(self):
        raw = (
            "accept: */*\nx-client-data: CJa2yQE=\nDecoded:\nmessage ClientVariations {\n"
            "  repeated int32 variation_id = [3300101];\n}\ncookie: SID=abc"
        )
        assert ytmusic.clean_headers(raw) == "accept: */*\nx-client-data: CJa2yQE=\ncookie: SID=abc"

    def test_count_parses_text(self):
        assert [ytmusic._count(v) for v in ("25", "1,204", None, "")] == [25, 1204, None, None]


class TestSpotify:
    ITEM = {
        "type": "track",
        "id": "1QoyuMHNBe7lg3YW4Qtll4",
        "uri": "spotify:track:1QoyuMHNBe7lg3YW4Qtll4",
        "name": "St. Chroma (feat. Daniel Caesar)",
        "artists": [{"name": "Tyler, The Creator"}, {"name": "Daniel Caesar"}],
        "album": {"name": "CHROMAKOPIA"},
        "duration_ms": 197019,
        "external_ids": {"isrc": "USQX92405783"},
        "is_local": False,
    }

    def test_candidate(self):
        c = spotify._to_candidate(self.ITEM)
        assert (c.id, c.artists, c.isrc) == (
            "1QoyuMHNBe7lg3YW4Qtll4",
            ["Tyler, The Creator", "Daniel Caesar"],
            "USQX92405783",
        )

    def test_track_id_is_the_uri_like_exportify(self):
        assert spotify._to_track(self.ITEM, 1).id == "spotify:track:1QoyuMHNBe7lg3YW4Qtll4"

    @pytest.mark.parametrize("key", ["item", "track"])
    def test_playlist_entry_new_and_old_key(self, key):
        assert spotify._entry_item({key: self.ITEM})["id"] == self.ITEM["id"]

    def test_get_playlist_skips_local_files_episodes_and_removed(self, tmp_path):
        provider = spotify.Spotify(Paths(tmp_path))
        entries = [
            {"item": self.ITEM},
            {"item": {**self.ITEM, "is_local": True, "id": None, "uri": "spotify:local:a:b:c:1"}},
            {"item": {**self.ITEM, "type": "episode"}},
            {"item": None},
        ]
        provider._get = lambda path, **kwargs: {"name": "Mix"}
        provider._pages = lambda path, **params: iter(entries)
        playlist = provider.get_playlist("pl")
        assert playlist.name == "Mix"
        assert [t.position for t in playlist.tracks] == [1]
        assert [t.position for t in playlist.skipped] == [2, 3, 4]

    def test_needs_login(self, tmp_path):
        with pytest.raises(AuthError, match="music-transfer login spotify"):
            spotify.Spotify(Paths(tmp_path)).list_playlists()


class TestDeezer:
    def test_full_track_lists_every_contributor(self):
        item = {
            "id": 3135556,
            "title": "Harder, Better, Faster, Stronger",
            "isrc": "GBDUW0000059",
            "duration": 224,
            "artist": {"name": "Daft Punk"},
            "contributors": [{"name": "Daft Punk"}, {"name": "Someone"}],
            "album": {"title": "Discovery"},
        }
        c = deezer._public_to_candidate(item)
        assert (c.id, c.artists, c.album, c.duration_ms, c.isrc) == (
            "3135556", ["Daft Punk", "Someone"], "Discovery", 224_000, "GBDUW0000059"
        )  # fmt: skip

    def test_search_result_has_only_the_main_artist(self):
        c = deezer._public_to_candidate({"id": 1, "title": "One More Time", "artist": {"name": "Daft Punk"}})
        assert (c.artists, c.duration_ms, c.isrc) == (["Daft Punk"], None, "")

    def test_gateway_song(self):
        song = {
            "SNG_ID": "3135556",
            "SNG_TITLE": "Digital Love",
            "VERSION": "(Remastered)",
            "ARTISTS": [{"ART_NAME": "Daft Punk"}],
            "ART_NAME": "Daft Punk",
            "ALB_TITLE": "Discovery",
            "DURATION": "301",
            "ISRC": "GBDUW0000060",
        }
        track = deezer._gw_to_track(song, 2)
        assert (track.id, track.title, track.artists, track.duration_ms, track.isrc, track.position) == (
            "deezer:3135556", "Digital Love (Remastered)", ["Daft Punk"], 301_000, "GBDUW0000060", 2
        )  # fmt: skip

    def test_quota_error_is_retried(self):
        from music_transfer.http import RetryableError

        with pytest.raises(RetryableError):
            deezer._check_public({"error": {"code": 4, "message": "Quota limit exceeded"}})
        deezer._check_public({"error": {"code": 800, "message": "no data"}})  # not found: not retried


class TestAppleMusic:
    def test_catalog_song(self):
        song = {
            "id": "1440841766",
            "type": "songs",
            "attributes": {
                "name": "Duality",
                "artistName": "Slipknot",
                "albumName": "Vol. 3: (The Subliminal Verses)",
                "durationInMillis": 252_000,
                "isrc": "NLA320400001",
            },
        }
        c = apple_music._to_candidate(song)
        assert (c.id, c.artists, c.duration_ms, c.isrc) == ("1440841766", ["Slipknot"], 252_000, "NLA320400001")

    def test_library_song_catalog_id(self):
        song = {"id": "i.abc", "attributes": {"playParams": {"id": "i.abc", "catalogId": "1440841766"}}}
        assert apple_music._catalog_id(song) == "1440841766"
        assert apple_music._catalog_id({"id": "i.upload", "attributes": {"playParams": {"id": "i.upload"}}}) == ""

    def test_cant_rename(self):
        assert apple_music.AppleMusic.can_rename is False


class TestSoundCloud:
    def test_label_track_uses_publisher_metadata(self):
        track = {
            "id": 123,
            "title": "Duality",
            "user": {"username": "slipknot-official"},
            "duration": 30_000,
            "full_duration": 252_000,
            "publisher_metadata": {"artist": "Slipknot", "album_title": "Vol. 3", "isrc": "NLA320400001"},
        }
        c = soundcloud._to_candidate(track)
        assert (c.id, c.artists, c.album, c.duration_ms, c.isrc) == (
            "123", ["Slipknot"], "Vol. 3", 252_000, "NLA320400001"
        )  # fmt: skip

    def test_upload_falls_back_to_the_uploader(self):
        c = soundcloud._to_candidate({"id": 5, "title": "My Remix", "user": {"username": "dj"}, "duration": 200_000})
        assert (c.artists, c.duration_ms, c.isrc) == (["dj"], 200_000, "")


def test_every_provider_has_a_unique_name_and_label():
    assert len({p.label for p in PROVIDERS.values()}) == len(PROVIDERS)
    assert all(name == p.name for name, p in PROVIDERS.items())
