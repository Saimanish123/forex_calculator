"""SQLite connection, migrations and backups."""

import logging
import shutil
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .config import BACKUPS_TO_KEEP, data_dir

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def db_path() -> Path:
    return data_dir() / "forex.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may run a request's dependency and endpoint on different worker threads. Each
    # request owns its connection and uses it sequentially, so cross-thread use is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the background rate warm-up write while requests read. Waits up to 10s on locks.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def to_db(value):
    """Python value → SQLite value. Decimals are stored as fixed-point text (never "1E+3")."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return f"{value:f}"
    if hasattr(value, "isoformat"):  # datetime / date
        return value.isoformat()
    return value


def migrate(conn: sqlite3.Connection) -> None:
    """Apply numbered .sql files above the database's user_version, in order."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(file.name.split("_", 1)[0])
        if version <= current:
            continue
        with conn:
            conn.executescript(file.read_text())
            conn.execute(f"PRAGMA user_version = {version}")


def backup(conn: sqlite3.Connection, extra_dir: str | None = None) -> Path:
    """Copy the database to data/backups (keeping the newest N), plus an optional extra folder."""
    backups = data_dir() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"forex-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
    dest.close()

    for old in sorted(backups.glob("forex-*.db"))[:-BACKUPS_TO_KEEP]:
        old.unlink()

    if extra_dir:
        try:
            extra = Path(extra_dir).expanduser()
            extra.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, extra / target.name)
        except OSError as exc:  # a bad extra folder must never block the app or the main backup
            log.warning("Couldn't copy backup to %s: %s", extra_dir, exc)
    return target
