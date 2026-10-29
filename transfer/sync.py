"""Criação das playlists no YT Music, adição das faixas, progresso e relatórios."""

import csv
import json
import time
from dataclasses import asdict
from pathlib import Path

from ytmusicapi import YTMusic

from .matcher import Match, find_match
from .sources import Playlist

ROOT = Path(__file__).resolve().parents[1]
AUTH_FILE = ROOT / "browser.json"
MATCHES_FILE = ROOT / "state" / "matches.json"
PLAYLISTS_FILE = ROOT / "state" / "playlists.json"
OUTPUT_DIR = ROOT / "output"

BATCH_SIZE = 50
DELAY = 0.7     # pausa entre chamadas, para não ser bloqueado pelo YouTube
RETRIES = 3

STATUS_ICON = {"matched": "✓", "low_confidence": "~", "not_found": "✗"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


def call(fn, *args, **kwargs):
    """Chama a API com pausa entre chamadas e novas tentativas em caso de erro."""
    for attempt in range(1, RETRIES + 1):
        try:
            result = fn(*args, **kwargs)
            time.sleep(DELAY)
            return result
        except Exception as error:
            if attempt == RETRIES:
                raise
            wait = 5 * attempt**2
            print(f"  ! erro: {error} — tentando de novo em {wait}s")
            time.sleep(wait)


class Transfer:
    def __init__(self, dry_run: bool, rematch: bool):
        self.dry_run = dry_run
        self.rematch = rematch
        # A busca funciona sem login, então o --dry-run não precisa do browser.json
        self.yt = YTMusic(str(AUTH_FILE)) if AUTH_FILE.exists() else YTMusic()
        self.matches = load_json(MATCHES_FILE)
        self.playlists = load_json(PLAYLISTS_FILE)

    def search(self, query: str, **kwargs) -> list[dict]:
        return call(self.yt.search, query, **kwargs)

    def target_name(self, playlist: Playlist, name: str | None) -> str:
        # Prioridade: --name > nome já usado antes > nome derivado do arquivo
        return name or self.playlists.get(playlist.file.name, {}).get("name") or playlist.name

    def match_tracks(self, playlist: Playlist) -> list[Match]:
        results = []
        total = len(playlist.tracks)
        try:
            for i, track in enumerate(playlist.tracks, start=1):
                cached = None if self.rematch else self.matches.get(track.uri)
                match = Match(**cached) if cached else find_match(track, self.search)
                if not cached:
                    self.matches[track.uri] = asdict(match)
                    if i % 10 == 0:
                        save_json(MATCHES_FILE, self.matches)
                results.append(match)
                found = f"{match.title} — {match.artists} ({match.score:g})" if match.accepted else "não encontrada"
                print(f"  [{i:>3}/{total}] {STATUS_ICON[match.status]} {track.title} — {track.artist}  →  {found}")
        finally:
            save_json(MATCHES_FILE, self.matches)
        return results

    def ensure_playlist(self, playlist: Playlist, name: str) -> str:
        saved = self.playlists.get(playlist.file.name)
        if saved:
            if saved["name"] != name:
                call(self.yt.edit_playlist, saved["id"], title=name)
                print(f"  playlist renomeada: {saved['name']} → {name}")
                saved["name"] = name
                save_json(PLAYLISTS_FILE, self.playlists)
            return saved["id"]

        existing = next((p for p in call(self.yt.get_library_playlists, limit=None) if p["title"] == name), None)
        if existing:
            playlist_id = existing["playlistId"]
            print(f"  usando a playlist que já existe no YT Music: {name}")
        else:
            # Sem nova tentativa: se a criação der timeout mas acontecer, repetir criaria uma duplicata.
            # Rodando de novo, a playlist é encontrada pelo nome logo acima.
            playlist_id = self.yt.create_playlist(name, "Importada do Spotify", privacy_status="PRIVATE")
            time.sleep(DELAY)
            if not isinstance(playlist_id, str):
                raise RuntimeError(f"Falha ao criar a playlist {name}: {playlist_id}")
            print(f"  playlist criada: {name}")

        self.playlists[playlist.file.name] = {"name": name, "id": playlist_id}
        save_json(PLAYLISTS_FILE, self.playlists)
        return playlist_id

    def add_tracks(self, playlist_id: str, matches: list[Match]) -> None:
        video_ids = list(dict.fromkeys(m.video_id for m in matches if m.accepted and m.video_id))
        # A própria playlist é o registro do progresso: o que já está lá não é enviado de novo
        current = call(self.yt.get_playlist, playlist_id, limit=None)
        present = {t.get("videoId") for t in current.get("tracks", [])}
        pending = [v for v in video_ids if v not in present]
        print(f"  {len(video_ids) - len(pending)} já na playlist, {len(pending)} para adicionar")

        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            result = call(self.yt.add_playlist_items, playlist_id, batch, duplicates=False)
            if not (isinstance(result, dict) and "SUCCEEDED" in str(result.get("status"))):
                raise RuntimeError(f"Falha ao adicionar músicas (rode de novo para continuar): {result}")
            print(f"  adicionadas {start + len(batch)}/{len(pending)}")

    def run(self, playlist: Playlist, name: str | None = None) -> None:
        name = self.target_name(playlist, name)
        print(f"\n== {name} ({len(playlist.tracks)} músicas)")
        matches = self.match_tracks(playlist)
        report = write_report(playlist, matches)

        counts = {status: sum(m.status == status for m in matches) for status in STATUS_ICON}
        print(
            f"  encontradas: {counts['matched']}, duvidosas: {counts['low_confidence']}, "
            f"não encontradas: {counts['not_found']}, ignoradas: {len(playlist.skipped)}"
        )
        print(f"  relatório: {report}")

        if self.dry_run:
            return
        playlist_id = self.ensure_playlist(playlist, name)
        self.add_tracks(playlist_id, matches)
        print(f"  pronto: https://music.youtube.com/playlist?list={playlist_id}")


def write_report(playlist: Playlist, matches: list[Match]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{playlist.file.stem}.csv"
    rows = [(t, m) for t, m in zip(playlist.tracks, matches)] + [(t, None) for t in playlist.skipped]
    rows.sort(key=lambda row: row[0].position)
    # ";" e utf-8-sig para abrir direto no Excel em português
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["posicao", "status", "nota", "spotify_titulo", "spotify_artistas",
                         "yt_titulo", "yt_artistas", "origem", "link"])
        for track, match in rows:
            if match is None:
                writer.writerow([track.position, "ignorada", "", track.title, ", ".join(track.artists), "", "", "", ""])
                continue
            link = f"https://music.youtube.com/watch?v={match.video_id}" if match.video_id else ""
            writer.writerow([track.position, match.status, match.score, track.title, ", ".join(track.artists),
                             match.title, match.artists, match.source, link])
    return path
