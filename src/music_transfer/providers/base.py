"""The interface every streaming service implements. See "Adding a provider" in the README."""

from typing import ClassVar

from ..matching import SearchStep
from ..models import Playlist, PlaylistRef, Track
from ..storage import Paths, load_json


class AuthError(RuntimeError):
    """Not logged in, login expired, or login failed. The message says what to do."""


class Provider:
    """A streaming service. Sources implement the reading methods, destinations the writing ones.

    Everything goes through the neutral models (Track, Playlist, Candidate), which is what lets
    any source work with any destination. Item ids given to and returned by the writing methods
    are the service's own ids, as found in Candidate.id."""

    name: ClassVar[str]  # CLI name, also used for file names: "spotify"
    label: ClassVar[str]  # display name: "Spotify"
    needs_login: ClassVar[bool] = True
    can_read: ClassVar[bool] = True
    can_write: ClassVar[bool] = True
    can_rename: ClassVar[bool] = True
    search_needs_login: ClassVar[bool] = True  # False lets --dry-run work without logging in
    batch_size: ClassVar[int] = 100  # items per add_items call

    def __init__(self, paths: Paths):
        self.paths = paths
        self.auth_file = paths.auth / f"{self.name}.json"
        self.credentials = load_json(self.auth_file)

    # --- login

    @classmethod
    def login(cls, paths: Paths) -> None:
        """Interactive login. Saves the credentials to .auth/<name>.json or raises AuthError."""
        raise NotImplementedError

    @property
    def logged_in(self) -> bool:
        return bool(self.credentials)

    def require_login(self) -> dict:
        if self.needs_login and not self.logged_in:
            raise AuthError(f"Not logged in to {self.label}. Run: music-transfer login {self.name}")
        return self.credentials

    # --- reading (source)

    def list_playlists(self) -> list[PlaylistRef]:
        raise NotImplementedError

    def get_playlist(self, playlist_id: str) -> Playlist:
        raise NotImplementedError

    def empty_message(self) -> str:
        return f"No playlists found on {self.label}."

    # --- writing (destination)

    def search_steps(self, track: Track) -> list[SearchStep]:
        """The searches to try for `track`, most precise first (see matching.find_match)."""
        raise NotImplementedError

    def find_playlist(self, name: str) -> str | None:
        return next((p.id for p in self.list_playlists() if p.name == name), None)

    def create_playlist(self, name: str, description: str) -> str:
        """Create a private playlist and return its id. Must not retry (see engine.ensure_playlist)."""
        raise NotImplementedError

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        raise NotImplementedError

    def playlist_item_ids(self, playlist_id: str) -> set[str]:
        raise NotImplementedError

    def add_items(self, playlist_id: str, item_ids: list[str]) -> None:
        """Append items (at most batch_size). Raise on failure; running again resumes."""
        raise NotImplementedError

    def item_url(self, item_id: str) -> str:
        raise NotImplementedError

    def playlist_url(self, playlist_id: str) -> str:
        raise NotImplementedError
