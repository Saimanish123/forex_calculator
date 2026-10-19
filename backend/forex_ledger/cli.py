"""`uv run forex` — back up the database, start the app and open the browser."""

import argparse
import threading
import webbrowser

import uvicorn

from . import db
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="forex", description="Run Forex Ledger locally.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()

    with db.connect() as conn:
        db.migrate(conn)
        backup_dir = conn.execute("SELECT value FROM settings WHERE key = 'backup_dir'").fetchone()
        db.backup(conn, backup_dir["value"] if backup_dir else None)
    conn.close()

    url = f"http://{args.host}:{args.port}"
    print(f"Forex Ledger is running at {url}  (Ctrl+C to stop)")
    print(f"Your data: {db.db_path()}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
