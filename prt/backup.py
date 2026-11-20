"""Daily DB backup for remote hosting: `python -m prt.backup`, run by the systemd timer."""

import sqlite3
from datetime import date
from pathlib import Path

from prt import db

BACKUP_DIR = db.DEFAULT_DB_PATH.parent / "backups"
KEEP = 14


def backup(
    db_path: Path = db.DEFAULT_DB_PATH, out_dir: Path = BACKUP_DIR, keep: int = KEEP
) -> Path:
    """Copy the DB to `out_dir/prt-<date>.db` and delete all but the newest `keep` copies."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"prt-{date.today().isoformat()}.db"
    # Read-only, so a missing DB fails instead of being created empty.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    # ISO dates sort chronologically, so the oldest names come first.
    for old in sorted(out_dir.glob("prt-*.db"))[:-keep]:
        old.unlink()
    return target


if __name__ == "__main__":
    print(backup())
