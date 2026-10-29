"""Scores how close a search result is to the source track and picks the best one. Provider-agnostic."""

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import Candidate, Track

CONFIDENT = 90  # score from which a result is considered certain
ACCEPT = 80  # minimum score to accept a song (reported as low_confidence)
ACCEPT_VIDEO = 85  # plain videos need a higher score than songs

# Parts in brackets that don't change the recording
_BRACKET_NOISE = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|radio edit|explicit|bonus|official|oficial|"
    r"video|clipe|lyrics?|letra|audio|visualizer|hd|4k)\b[^\)\]]*[\)\]]"
)
# Suffixes after " - " that don't change the recording
_DASH_NOISE = re.compile(
    r"\s-\s.*\b(remaster(ed)?|radio edit|bonus track|single version|album version|mono|stereo|"
    r"spotify singles?|spotify sessions?)\b.*$"
)
_FEAT = re.compile(r"\s(feat|ft)\.?\s.*$")
_CHANNEL_NOISE = re.compile(r"\b(vevo|topic|official|oficial)\b")
# Third-party or altered versions; only acceptable if the source title has the same word
_UNWANTED = re.compile(
    r"\b(cover|tabs?|karaoke|instrumental|reaction|react|tutorial|nightcore|slowed|sped up|8d|"
    r"como tocar|playthrough|backing track|drum cam|guitar cam|bass cam)\b"
)


@dataclass
class Match:
    item_id: str | None
    title: str
    artists: str
    score: float
    source: str  # the search step that found it: "isrc", "songs", "videos"…
    accepted: bool = True  # False: best candidate found, but its score is too low

    @property
    def status(self) -> str:
        if not self.accepted or self.item_id is None:
            return "not_found"
        return "matched" if self.score >= CONFIDENT else "low_confidence"


@dataclass
class SearchStep:
    """One search a destination tries for a track. Steps run in order; see find_match."""

    source: str  # label stored in the report
    run: Callable[[], list[Candidate]]
    accept: float = ACCEPT  # minimum score to accept a result from this step
    skip_if: float = CONFIDENT  # skip this step when an accepted match already scores this much


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize(text: str) -> str:
    text = _plain(text)
    text = _BRACKET_NOISE.sub(" ", text)
    text = _DASH_NOISE.sub(" ", text)
    text = _FEAT.sub(" ", text)
    text = re.sub(r"[^\w ]+", " ", text)
    return " ".join(text.split())


def search_query(track: Track) -> str:
    return f"{normalize(track.title)} {track.artist}"


def _similarity(a: str, b: str) -> float:
    # token_set alone gives 100 for "snuff" x "snuff remix"; averaging with token_sort penalizes the difference
    return (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 2


def score(track: Track, candidate: Candidate) -> float:
    title = normalize(track.title)
    found_title = normalize(candidate.title)
    # Videos and SoundCloud uploads often have the artist in the title ("Slipknot - Snuff (Official Video)")
    title_score = max(_similarity(title, found_title), _similarity(f"{normalize(track.artist)} {title}", found_title))

    artists = [normalize(a) for a in track.artists]
    found_artists = [_CHANNEL_NOISE.sub(" ", normalize(a)).strip() for a in candidate.artists]
    artist_score = max((fuzz.token_set_ratio(a, b) for a in artists for b in found_artists if a and b), default=0)
    if any(a and a in found_title for a in artists):
        artist_score = max(artist_score, 90)

    total = 0.6 * title_score + 0.4 * artist_score

    unwanted = {m.group() for m in _UNWANTED.finditer(_plain(candidate.title))}
    if unwanted - {m.group() for m in _UNWANTED.finditer(_plain(track.title))}:
        total -= 30

    if candidate.album and fuzz.token_set_ratio(normalize(candidate.album), normalize(track.album)) >= 90:
        total += 5

    if track.duration_ms and candidate.duration_ms:
        diff = abs(track.duration_ms - candidate.duration_ms) / 1000
        tolerance = 30 if candidate.kind == "video" else 5  # music videos often have an intro/outro
        if diff > tolerance:
            total -= min(25, (diff - tolerance) * 0.5)

    total = max(0.0, min(100.0, total))
    if track.isrc and candidate.isrc and track.isrc.upper() == candidate.isrc.upper():
        total = max(total, CONFIDENT)  # same ISRC is the same recording, whatever the title says
    return round(total, 1)


def _best(track: Track, candidates: list[Candidate], source: str) -> Match | None:
    scored = [Match(c.id, c.title, ", ".join(c.artists), score(track, c), source) for c in candidates if c.id]
    return max(scored, key=lambda m: m.score, default=None)


def find_match(track: Track, steps: Iterable[SearchStep]) -> Match:
    """Run the destination's search steps in order and return the best accepted result.

    A step is skipped once an accepted match already scores its `skip_if`, so precise steps
    (ISRC lookups) go first and fallbacks (YouTube videos) only run when needed. When nothing
    is accepted, the best candidate is still returned (accepted=False) so the report can show it."""
    best: Match | None = None
    fallback: Match | None = None
    for step in steps:
        if best and best.score >= step.skip_if:
            continue
        found = _best(track, step.run(), step.source)
        if found is None:
            continue
        if fallback is None or found.score > fallback.score:
            fallback = found
        if found.score >= step.accept and (best is None or found.score > best.score):
            best = found

    if best:
        return best
    if fallback is None:
        return Match(None, "", "", 0.0, "", accepted=False)
    fallback.accepted = False
    return fallback
