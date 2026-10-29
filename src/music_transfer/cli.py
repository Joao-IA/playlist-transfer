"""Move playlists between music streaming services.

music-transfer login <service>
music-transfer playlists <service>
music-transfer transfer --from <service> --to <service> [--playlist NAME]... [--name NAME] [--dry-run] [--rematch]
"""

import argparse
import sys
from pathlib import Path

import requests

from .engine import Transfer
from .models import PlaylistRef
from .providers import PROVIDERS
from .providers.base import AuthError, Provider
from .storage import Paths


def select(refs: list[PlaylistRef], wanted: list[str]) -> list[PlaylistRef]:
    """Pick playlists by id or name, ignoring case ("Rock_Foda" also matches "Rock Foda"). All if none given."""
    if not wanted:
        return refs
    selected: list[PlaylistRef] = []
    for value in wanted:
        value = value.lower()
        names = {value, value.replace("_", " ")}
        found = [r for r in refs if r.id.lower() == value or r.name.lower() in names]
        if not found:
            raise LookupError(value)
        selected += [r for r in found if r not in selected]
    return selected


def build_parser() -> argparse.ArgumentParser:
    readable = [n for n, p in PROVIDERS.items() if p.can_read]
    writable = [n for n, p in PROVIDERS.items() if p.can_write]
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--csv-dir", type=Path, help="folder with Exportify CSVs (default: exports/)")

    parser = argparse.ArgumentParser(prog="music-transfer", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="log in to a service")
    login.add_argument("service", choices=[n for n, p in PROVIDERS.items() if p.needs_login])

    playlists = commands.add_parser("playlists", parents=[common], help="list the playlists on a service")
    playlists.add_argument("service", choices=readable)

    transfer = commands.add_parser("transfer", parents=[common], help="copy playlists from one service to another")
    transfer.add_argument("--from", dest="source", required=True, choices=readable)
    transfer.add_argument("--to", dest="dest", required=True, choices=writable)
    transfer.add_argument("--playlist", action="append", default=[], help="name or id; repeatable (default: all)")
    transfer.add_argument("--name", help="exact playlist name on the destination (needs a single --playlist)")
    transfer.add_argument("--dry-run", action="store_true", help="only search and write the report; change nothing")
    transfer.add_argument("--rematch", action="store_true", help="ignore cached searches and search everything again")
    return parser


def list_playlists(provider: Provider) -> int:
    refs = provider.list_playlists()
    if not refs:
        print(provider.empty_message())
        return 1
    width = max(len(r.name) for r in refs)
    for r in refs:
        count = f"{r.track_count:>5} tracks" if r.track_count is not None else " " * 12
        print(f"{r.name:<{width}}  {count}   [{r.id}]")
    return 0


def transfer(parser: argparse.ArgumentParser, args: argparse.Namespace, paths: Paths) -> int:
    if args.source == args.dest:
        parser.error("--from and --to must be different services")
    if args.name and len(args.playlist) != 1:
        parser.error("--name needs exactly one --playlist")

    source = PROVIDERS[args.source](paths)
    dest = PROVIDERS[args.dest](paths)
    source.require_login()
    if not args.dry_run or dest.search_needs_login:
        dest.require_login()

    refs = source.list_playlists()
    if not refs:
        print(source.empty_message())
        return 1
    try:
        refs = select(refs, args.playlist)
    except LookupError as error:
        parser.error(f"playlist not found on {source.label}: {error}")
    if args.name and len(refs) != 1:
        parser.error(f"--name matches {len(refs)} playlists; pick one by id")

    job = Transfer(source, dest, paths, dry_run=args.dry_run, rematch=args.rematch)
    for ref in refs:
        job.run(source.get_playlist(ref.id), args.name)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page that can't print every song title
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = Paths.default(getattr(args, "csv_dir", None))

    try:
        if args.command == "login":
            PROVIDERS[args.service].login(paths)
            return 0
        if args.command == "playlists":
            return list_playlists(PROVIDERS[args.service](paths))
        return transfer(parser, args, paths)
    except AuthError as error:
        print(error)
        return 1
    except requests.HTTPError as error:
        print(f"Request failed: {error}")
        if error.response is not None and error.response.status_code in (401, 403):
            print("Your login may have expired. Log in again with: music-transfer login <service>")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command again to continue where it stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
