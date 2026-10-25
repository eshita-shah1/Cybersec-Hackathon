"""Initialize the database and seed the example agents (idempotent).

Usage (from the d3coy-backend directory):
    python scripts/seed_data.py            # create tables + insert missing agents
    python scripts/seed_data.py --reset    # DELETE the local SQLite database first (asks for confirmation)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database.db import Database  # noqa: E402
from app.registry.agent_registry import AgentRegistry  # noqa: E402


def sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    return Path(url[len(prefix):]) if url.startswith(prefix) and ":memory:" not in url else None


def reset_database(url: str, assume_yes: bool) -> None:
    path = sqlite_path(url)
    if path is None:
        raise SystemExit("--reset is only supported for file-based SQLite databases")
    if not assume_yes:
        answer = input(f"This permanently deletes {path.resolve()} (all events and audit records). Type 'yes': ")
        if answer.strip().lower() != "yes":
            raise SystemExit("Aborted.")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    print(f"Deleted {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the local SQLite database first")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation with --reset")
    args = parser.parse_args()
    settings = get_settings()
    if args.reset:
        reset_database(settings.database_url, args.yes)
    db = Database(settings.database_url)
    db.create_all()
    created = AgentRegistry(db).seed()
    print(f"Database ready at {settings.database_url}")
    print(f"Seeded agents: {', '.join(created) if created else 'none (already present)'}")
    for agent in AgentRegistry(db).list():
        print(f"  {agent.agent_id:<20} {agent.role:<18} {agent.status.value}")


if __name__ == "__main__":
    main()
