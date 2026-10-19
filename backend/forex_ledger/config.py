import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"

IST = ZoneInfo("Asia/Kolkata")

BACKUPS_TO_KEEP = 30


def data_dir() -> Path:
    """Where the database and backups live. Override with FOREX_LEDGER_DATA_DIR."""
    return Path(os.environ.get("FOREX_LEDGER_DATA_DIR", ROOT_DIR / "data"))
