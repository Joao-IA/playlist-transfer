"""Service-neutral data model. Every provider converts to and from these, so any source works with any destination."""

from dataclasses import dataclass, field


@dataclass
class Track:
    """A track read from the source service."""

    id: str  # namespaced by service ("spotify:track:…", "deezer:3135556"), so caches never mix services
    title: str
    artists: list[str]
    album: str = ""
    duration_ms: int | None = None
    isrc: str = ""
    position: int = 0

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else ""


@dataclass
class Playlist:
    id: str
    name: str
    tracks: list[Track]
    skipped: list[Track] = field(default_factory=list)  # can't be transferred: local files, removed tracks, uploads


@dataclass
class PlaylistRef:
    """A playlist as it appears in a listing, before its tracks are loaded."""

    id: str
    name: str
    track_count: int | None = None


@dataclass
class Candidate:
    """A search result on the destination service. `id` is that service's own id."""

    id: str
    title: str
    artists: list[str]
    album: str = ""
    duration_ms: int | None = None
    isrc: str = ""
    kind: str = "song"  # "song" or "video" (plain YouTube videos get more duration tolerance)
