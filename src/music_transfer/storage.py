"""Where everything lives on disk, and small JSON helpers."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    """Project folders. Everything is relative to `home`, the working directory by default."""

    home: Path
    csv_dir: Path | None = None  # --csv-dir overrides exports/

    @property
    def auth(self) -> Path:
        return self.home / ".auth"  # credentials; never commit

    @property
    def state(self) -> Path:
        return self.home / "state"  # search cache and created playlist ids, per destination

    @property
    def output(self) -> Path:
        return self.home / "output"  # CSV reports

    @property
    def exports(self) -> Path:
        return self.csv_dir or self.home / "exports"  # Exportify CSVs to transfer

    @classmethod
    def default(cls, csv_dir: Path | None = None) -> "Paths":
        return cls(Path(os.environ.get("MUSIC_TRANSFER_HOME") or Path.cwd()), csv_dir)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def save_json(path: Path, data: dict, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    if private:
        tmp.chmod(0o600)  # credentials: readable by the owner only (no-op on Windows)
    tmp.replace(path)
