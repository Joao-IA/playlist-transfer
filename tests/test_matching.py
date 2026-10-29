import pytest

from music_transfer import matching
from music_transfer.matching import ACCEPT, ACCEPT_VIDEO, SearchStep, find_match, normalize, score
from music_transfer.models import Candidate, Track

DUALITY = Track("spotify:track:1", "Duality", ["Slipknot"], album="Vol. 3", duration_ms=252_000)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Snuff (Remastered 2009)", "snuff"),
        ("Numb - 2020 Remaster", "numb"),
        ("St. Chroma (feat. Daniel Caesar)", "st chroma"),
        ("Song feat. Someone Else", "song"),
        ("Coração Selvagem", "coracao selvagem"),
        ("Duality [Official Video]", "duality"),
    ],
)
def test_normalize_drops_noise_that_doesnt_change_the_recording(raw, expected):
    assert normalize(raw) == expected


def test_exact_match_scores_100():
    assert score(DUALITY, Candidate("c", "Duality", ["Slipknot"], "Vol. 3", 252_000)) == 100


def test_video_title_with_artist_still_matches():
    video = Candidate("c", "Slipknot - Duality (Official Video)", ["SlipknotVEVO"], duration_ms=260_000, kind="video")
    assert score(DUALITY, video) >= ACCEPT_VIDEO


def test_cover_is_rejected():
    assert score(DUALITY, Candidate("c", "Duality (Cover)", ["Slipknot"], duration_ms=252_000)) < ACCEPT


def test_cover_is_fine_when_the_source_is_a_cover_too():
    cover = Track("t", "Duality (Cover)", ["Some Band"])
    assert score(cover, Candidate("c", "Duality (Cover)", ["Some Band"])) == 100


def test_remix_scores_below_the_original():
    original = score(DUALITY, Candidate("c", "Duality", ["Slipknot"]))
    remix = score(DUALITY, Candidate("c", "Duality (Remix)", ["Slipknot"]))
    assert remix < original


def test_wrong_duration_is_penalized():
    assert score(DUALITY, Candidate("c", "Duality", ["Slipknot"], duration_ms=252_000 + 65_000)) < ACCEPT


def test_videos_get_more_duration_tolerance():
    assert score(DUALITY, Candidate("c", "Duality", ["Slipknot"], duration_ms=272_000, kind="video")) == 100
    assert score(DUALITY, Candidate("c", "Duality", ["Slipknot"], duration_ms=272_000)) < 100


def test_same_isrc_is_confident_whatever_the_title():
    track = Track("t", "Duality", ["Slipknot"], isrc="NLA320400001")
    assert score(track, Candidate("c", "Duality (Cover)", ["Slipknot"], isrc="nla320400001")) == matching.CONFIDENT


class TestFindMatch:
    """The step logic, with candidates whose title is their score."""

    @pytest.fixture(autouse=True)
    def score_from_title(self, monkeypatch):
        monkeypatch.setattr(matching, "score", lambda track, c: float(c.title))

    @staticmethod
    def youtube_steps(calls, songs=(), isrc=(), videos=()):
        def step(source, scores, **kwargs):
            def run():
                calls.append(source)
                return [Candidate(f"{source}-{i}", str(s), []) for i, s in enumerate(scores)]

            return SearchStep(source, run, **kwargs)

        return [
            step("songs", songs),
            step("isrc", isrc),
            step("videos", videos, accept=ACCEPT_VIDEO, skip_if=ACCEPT),
        ]

    def test_confident_song_skips_the_other_steps(self):
        calls = []
        match = find_match(DUALITY, self.youtube_steps(calls, songs=[70, 95]))
        assert calls == ["songs"]
        assert (match.item_id, match.status) == ("songs-1", "matched")

    def test_uncertain_song_tries_isrc_but_not_videos(self):
        calls = []
        match = find_match(DUALITY, self.youtube_steps(calls, songs=[85], isrc=[92], videos=[99]))
        assert calls == ["songs", "isrc"]
        assert (match.source, match.score) == ("isrc", 92)

    def test_videos_only_when_no_song_was_accepted(self):
        calls = []
        match = find_match(DUALITY, self.youtube_steps(calls, songs=[60], isrc=[70], videos=[88]))
        assert calls == ["songs", "isrc", "videos"]
        assert (match.source, match.accepted) == ("videos", True)

    def test_videos_need_a_higher_score(self):
        match = find_match(DUALITY, self.youtube_steps([], songs=[60], videos=[82]))
        assert match.status == "not_found"
        assert (match.item_id, match.score) == ("videos-0", 82)  # kept for the report

    def test_tie_keeps_the_earlier_step(self):
        match = find_match(DUALITY, self.youtube_steps([], songs=[85], isrc=[85]))
        assert match.source == "songs"

    def test_nothing_found(self):
        match = find_match(DUALITY, self.youtube_steps([]))
        assert (match.item_id, match.status) == (None, "not_found")
