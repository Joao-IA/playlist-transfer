"""Busca de cada faixa no YT Music e escolha do melhor resultado."""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from .sources import Track

CONFIDENT = 90     # nota a partir da qual o resultado é considerado certo
ACCEPT_SONG = 80   # nota mínima para aceitar uma "song" (com status low_confidence)
ACCEPT_VIDEO = 85  # vídeos comuns precisam de nota maior que songs

# Trechos entre parênteses/colchetes que não mudam a gravação
_BRACKET_NOISE = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|radio edit|explicit|bonus|official|oficial|"
    r"video|clipe|lyrics?|letra|audio|visualizer|hd|4k)\b[^\)\]]*[\)\]]"
)
# Sufixos depois de " - " que não mudam a gravação
_DASH_NOISE = re.compile(
    r"\s-\s.*\b(remaster(ed)?|radio edit|bonus track|single version|album version|mono|stereo|"
    r"spotify singles?|spotify sessions?)\b.*$"
)
_FEAT = re.compile(r"\s(feat|ft)\.?\s.*$")
_CHANNEL_NOISE = re.compile(r"\b(vevo|topic|official|oficial)\b")
# Versões feitas por terceiros ou alteradas; só servem se o título do Spotify também tiver a palavra
_UNWANTED = re.compile(
    r"\b(cover|tabs?|karaoke|instrumental|reaction|react|tutorial|nightcore|slowed|sped up|8d|"
    r"como tocar|playthrough|backing track|drum cam|guitar cam|bass cam)\b"
)


@dataclass
class Match:
    video_id: str | None
    title: str
    artists: str
    score: float
    source: str  # songs, isrc ou videos
    accepted: bool = True  # False: melhor candidato encontrado, mas com nota baixa demais

    @property
    def status(self) -> str:
        if not self.accepted or self.video_id is None:
            return "not_found"
        return "matched" if self.score >= CONFIDENT else "low_confidence"


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


def _similarity(a: str, b: str) -> float:
    # token_set sozinho dá 100 para "snuff" x "snuff remix"; a média com token_sort penaliza a diferença
    return (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 2


def score(track: Track, result: dict, is_video: bool) -> float:
    title = normalize(track.title)
    yt_title = normalize(result.get("title") or "")
    # Vídeos costumam ter o artista no título ("Slipknot - Snuff (Official Video)")
    title_score = max(_similarity(title, yt_title), _similarity(f"{normalize(track.artist)} {title}", yt_title))

    artists = [normalize(a) for a in track.artists]
    yt_artists = [_CHANNEL_NOISE.sub(" ", normalize(a["name"])).strip() for a in result.get("artists") or []]
    artist_score = max((fuzz.token_set_ratio(a, b) for a in artists for b in yt_artists if a and b), default=0)
    if any(a and a in yt_title for a in artists):
        artist_score = max(artist_score, 90)

    total = 0.6 * title_score + 0.4 * artist_score

    unwanted = {m.group() for m in _UNWANTED.finditer(_plain(result.get("title") or ""))}
    if unwanted - {m.group() for m in _UNWANTED.finditer(_plain(track.title))}:
        total -= 30

    album = (result.get("album") or {}).get("name")
    if album and fuzz.token_set_ratio(normalize(album), normalize(track.album)) >= 90:
        total += 5

    seconds = result.get("duration_seconds")
    if track.duration_ms and seconds:
        diff = abs(track.duration_ms / 1000 - seconds)
        tolerance = 30 if is_video else 5  # clipes costumam ter intro/outro
        if diff > tolerance:
            total -= min(25, (diff - tolerance) * 0.5)

    return round(max(0.0, min(100.0, total)), 1)


def _best(track: Track, results: list[dict], source: str) -> Match | None:
    is_video = source == "videos"
    scored = [
        Match(
            r["videoId"],
            r.get("title") or "",
            ", ".join(a["name"] for a in r.get("artists") or []),
            score(track, r, is_video),
            source,
        )
        for r in results
        if r.get("videoId")
    ]
    return max(scored, key=lambda m: m.score, default=None)


def find_match(track: Track, search) -> Match:
    """Busca em cascata: songs por texto → songs por ISRC → vídeos. `search` é o yt.search com retry."""
    query = f"{normalize(track.title)} {track.artist}"
    candidates = []

    candidates.append(_best(track, search(query, filter="songs", limit=5), "songs"))
    if track.isrc and max((c.score for c in candidates if c), default=0) < CONFIDENT:
        candidates.append(_best(track, search(track.isrc, filter="songs", limit=3), "isrc"))

    best = max((c for c in candidates if c), key=lambda m: m.score, default=None)
    if best and best.score >= ACCEPT_SONG:
        return best

    video = _best(track, search(query, filter="videos", limit=5), "videos")
    if video and video.score >= ACCEPT_VIDEO:
        return video

    # Não encontrado: guarda o melhor candidato só para consulta no relatório
    fallback = max((c for c in [best, video] if c), key=lambda m: m.score, default=None)
    if fallback is None:
        return Match(None, "", "", 0.0, "", accepted=False)
    fallback.accepted = False
    return fallback
