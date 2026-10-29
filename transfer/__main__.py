"""Transfere playlists do Spotify (CSVs do Exportify) para o YouTube Music.

    python -m transfer login
    python -m transfer list
    python -m transfer run [--playlist NOME] [--name NOME] [--dry-run] [--rematch]
"""

import argparse
import json
import re
import sys

from ytmusicapi import YTMusic, setup

from .sources import load_playlists
from .sync import AUTH_FILE, ROOT, Transfer

HEADERS_FILE = ROOT / "headers.txt"


def clean_headers(raw: str) -> str:
    """Remove o bloco "Decodificados:" (x-client-data) que o Chrome inclui e o ytmusicapi não reconhece."""
    lines, skipping = [], False
    for line in raw.splitlines():
        if line.strip().lower() in ("decodificados:", "decoded:"):
            skipping = True
        elif skipping:
            skipping = line.strip() != "}"
        else:
            lines.append(line)
    return "\n".join(lines)


def login() -> int:
    if not HEADERS_FILE.exists() or not HEADERS_FILE.read_text("utf-8").strip():
        print(f"Cole os cabeçalhos da requisição do YT Music em {HEADERS_FILE}, salve e rode de novo (veja o README.md).")
        return 1
    try:
        headers = json.loads(setup(headers_raw=clean_headers(HEADERS_FILE.read_text("utf-8"))))
        # Pseudo-cabeçalhos do Chrome (":path", ":authority") viram chaves inválidas, e com
        # "content-encoding: gzip" o YouTube espera o corpo comprimido e responde com erro
        headers = {k: v for k, v in headers.items() if re.fullmatch(r"[a-z0-9-]+", k) and k != "content-encoding"}
        AUTH_FILE.write_text(json.dumps(headers, indent=4, sort_keys=True), "utf-8")
        playlists = YTMusic(str(AUTH_FILE)).get_library_playlists(limit=None)
    except Exception as error:
        AUTH_FILE.unlink(missing_ok=True)
        print(f"Falha no login: {error}\nCopie os cabeçalhos de novo (requisição POST 'browse', logado) e tente outra vez.")
        return 1
    HEADERS_FILE.unlink()  # contém os cookies da sessão; o browser.json já tem o necessário
    print(f"Login OK ({len(playlists)} playlists na biblioteca). {AUTH_FILE.name} criado e headers.txt apagado.")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m transfer", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="gera o browser.json a partir do headers.txt")
    commands.add_parser("list", help="mostra as playlists encontradas nos CSVs")
    run = commands.add_parser("run", help="transfere as playlists para o YT Music")
    run.add_argument("--playlist", help="transfere só esta playlist (nome ou nome do arquivo)")
    run.add_argument("--name", help="nome exato da playlist no YT Music (exige --playlist)")
    run.add_argument("--dry-run", action="store_true", help="só busca as músicas e gera o relatório, sem criar nada")
    run.add_argument("--rematch", action="store_true", help="ignora as buscas salvas e procura tudo de novo")
    args = parser.parse_args()

    if args.command == "login":
        return login()

    playlists = load_playlists(ROOT)
    if not playlists:
        print(f"Nenhum CSV encontrado em {ROOT}")
        return 1

    if args.command == "list":
        for p in playlists:
            skipped = f" (+{len(p.skipped)} ignoradas)" if p.skipped else ""
            print(f"{p.name:<30} {len(p.tracks):>4} músicas{skipped}   [{p.file.name}]")
        return 0

    if args.name and not args.playlist:
        parser.error("--name exige --playlist")
    if args.playlist:
        wanted = args.playlist.lower()
        playlists = [p for p in playlists if wanted in (p.name.lower(), p.file.stem.lower(), p.file.name.lower())]
        if not playlists:
            parser.error(f"playlist não encontrada: {args.playlist}")

    if not args.dry_run and not AUTH_FILE.exists():
        print("Falta o browser.json com o login do YT Music. Veja o passo 2 do README.md.")
        return 1

    transfer = Transfer(dry_run=args.dry_run, rematch=args.rematch)
    try:
        for playlist in playlists:
            transfer.run(playlist, args.name)
    except KeyboardInterrupt:
        print("\nInterrompido. Rode o mesmo comando de novo para continuar de onde parou.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
