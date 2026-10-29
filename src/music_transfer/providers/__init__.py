"""Registry of supported services. To add one, subclass providers.base.Provider and list it here."""

from .apple_music import AppleMusic
from .base import Provider
from .csv_exportify import ExportifyCsv
from .deezer import Deezer
from .soundcloud import SoundCloud
from .spotify import Spotify
from .ytmusic import YouTubeMusic

PROVIDERS: dict[str, type[Provider]] = {
    p.name: p for p in (ExportifyCsv, Spotify, YouTubeMusic, Deezer, AppleMusic, SoundCloud)
}
