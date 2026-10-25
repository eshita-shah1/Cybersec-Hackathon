"""Database engine and session management (SQLite by default)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and ":memory:" not in url:
        path = Path(url[len(prefix):])
        path.parent.mkdir(parents=True, exist_ok=True)


class Database:
    """Owns the SQLAlchemy engine. Each unit of work uses its own short-lived session."""

    def __init__(self, url: str) -> None:
        self.url = url
        is_sqlite = url.startswith("sqlite")
        if is_sqlite:
            _ensure_sqlite_dir(url)
        connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if is_sqlite:
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from app.database import models  # noqa: F401  (registers tables)

        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    # Columns added after the first release. create_all() creates new tables but never alters
    # existing ones, so older local databases get these columns added here.
    _ADDED_COLUMNS = {"pending_reviews": {"expires_at": "DATETIME"}}

    def _add_missing_columns(self) -> None:
        if not self.url.startswith("sqlite"):
            return
        with self.engine.begin() as conn:
            for table, cols in self._ADDED_COLUMNS.items():
                existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
                for col, ddl in cols.items():
                    if existing and col not in existing:
                        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                        logger.info("Added column %s.%s", table, col)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commit on success, roll back on any exception."""
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        except Exception:  # pragma: no cover - reported via status endpoint
            logger.exception("Database ping failed")
            return False

    def dispose(self) -> None:
        self.engine.dispose()


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
