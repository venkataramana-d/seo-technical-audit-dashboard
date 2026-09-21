"""Engine/session factory. Reads DATABASE_URL; defaults to a local SQLite
file for dev so no external DB install is required. Swapping to Postgres in
production is `DATABASE_URL=postgresql://...` - no code change.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent.parent / "dev.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DEFAULT_SQLITE_PATH}")

# `check_same_thread=False` is required for SQLite when the worker's polling
# loop and any request-handling code might touch the engine from different
# threads; harmless no-op for Postgres.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    """One-shot session for scripts/CLI use. Callers are responsible for
    closing it (use as a context manager: `with get_session() as db:`)."""
    return SessionLocal()
