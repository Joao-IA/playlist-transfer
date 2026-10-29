"""Runs a transfer: match every track, make sure the playlist exists, add what's missing."""

from dataclasses import asdict

from .matching import Match, find_match
from .models import Playlist
from .providers.base import Provider
from .report import write_report
from .storage import Paths, load_json, save_json

STATUS_ICON = {"matched": "✓", "low_confidence": "~", "not_found": "✗"}


class Transfer:
    def __init__(self, source: Provider, dest: Provider, paths: Paths, dry_run: bool = False, rematch: bool = False):
        self.source = source
        self.dest = dest
        self.dry_run = dry_run
        self.rematch = rematch
        state = paths.state / dest.name
        self.matches_file = state / "matches.json"  # source track id -> Match, reused by every playlist
        self.playlists_file = state / "playlists.json"  # "<source>:<playlist id>" -> playlist on the destination
        self.report_dir = paths.output / f"{source.name}-to-{dest.name}"
        self.matches = load_json(self.matches_file)
        self.playlists = load_json(self.playlists_file)

    def key(self, playlist: Playlist) -> str:
        return f"{self.source.name}:{playlist.id}"

    def target_name(self, playlist: Playlist, name: str | None) -> str:
        # Priority: --name > name used on a previous run > source playlist name
        return name or self.playlists.get(self.key(playlist), {}).get("name") or playlist.name

    def match_tracks(self, playlist: Playlist) -> list[Match]:
        results = []
        total = len(playlist.tracks)
        try:
            for i, track in enumerate(playlist.tracks, start=1):
                cached = None if self.rematch else self.matches.get(track.id)
                match = Match(**cached) if cached else find_match(track, self.dest.search_steps(track))
                if not cached:
                    self.matches[track.id] = asdict(match)
                    if i % 10 == 0:
                        save_json(self.matches_file, self.matches)
                results.append(match)
                found = f"{match.title} — {match.artists} ({match.score:g})" if match.accepted else "not found"
                print(f"  [{i:>3}/{total}] {STATUS_ICON[match.status]} {track.title} — {track.artist}  →  {found}")
        finally:
            save_json(self.matches_file, self.matches)
        return results

    def ensure_playlist(self, playlist: Playlist, name: str) -> str:
        key = self.key(playlist)
        saved = self.playlists.get(key)
        if saved:
            if saved["name"] != name:
                if self.dest.can_rename:
                    self.dest.rename_playlist(saved["id"], name)
                    print(f"  playlist renamed: {saved['name']} → {name}")
                    saved["name"] = name
                    save_json(self.playlists_file, self.playlists)
                else:
                    print(f'  {self.dest.label} can\'t rename playlists through its API; keeping "{saved["name"]}"')
            return saved["id"]

        playlist_id = self.dest.find_playlist(name)
        if playlist_id:
            print(f"  using the playlist that already exists on {self.dest.label}: {name}")
        else:
            # Providers create without retrying: if the request times out after the playlist was
            # created, a retry would make a duplicate. Running again finds it by name just above.
            playlist_id = self.dest.create_playlist(name, f"Imported from {self.source.label} with music-transfer")
            print(f"  playlist created: {name}")

        self.playlists[key] = {"name": name, "id": playlist_id}
        save_json(self.playlists_file, self.playlists)
        return playlist_id

    def add_tracks(self, playlist_id: str, matches: list[Match]) -> None:
        item_ids = list(dict.fromkeys(m.item_id for m in matches if m.accepted and m.item_id))
        # The playlist itself is the progress record: what's already there isn't sent again
        present = self.dest.playlist_item_ids(playlist_id)
        pending = [i for i in item_ids if i not in present]
        print(f"  {len(item_ids) - len(pending)} already in the playlist, {len(pending)} to add")

        size = self.dest.batch_size
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            self.dest.add_items(playlist_id, batch)
            print(f"  added {start + len(batch)}/{len(pending)}")

    def run(self, playlist: Playlist, name: str | None = None) -> None:
        name = self.target_name(playlist, name)
        print(f"\n== {name} ({len(playlist.tracks)} tracks, {self.source.label} → {self.dest.label})")
        matches = self.match_tracks(playlist)
        report = write_report(self.report_dir, playlist, matches, self.dest.item_url)

        counts = {status: sum(m.status == status for m in matches) for status in STATUS_ICON}
        print(
            f"  matched: {counts['matched']}, uncertain: {counts['low_confidence']}, "
            f"not found: {counts['not_found']}, skipped: {len(playlist.skipped)}"
        )
        print(f"  report: {report}")

        if self.dry_run:
            return
        playlist_id = self.ensure_playlist(playlist, name)
        self.add_tracks(playlist_id, matches)
        print(f"  done: {self.dest.playlist_url(playlist_id)}")
