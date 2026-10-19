"""Where LangGraph keeps conversation state, so a paused confirmation survives restarts.

Postgres (Neon, in production) → PostgresSaver; SQLite (laptop, tests) → SqliteSaver.
Tables are created on first use (`setup()`), separate from our Alembic-managed tables.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import _normalise


def make_checkpointer(database_url: str):
    url = _normalise(database_url)
    if url.startswith("postgresql"):
        import psycopg
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver

        conninfo = url.replace("postgresql+psycopg://", "postgresql://", 1)
        conn = psycopg.Connection.connect(conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = url.removeprefix("sqlite:///") or "checkpoints.db"
    if path in (":memory:", ""):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        db = Path(path)
        conn = sqlite3.connect(str(db.with_name(db.stem + "-checkpoints.db")), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
