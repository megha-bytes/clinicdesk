"""Where LangGraph keeps conversation state, so a paused confirmation survives restarts.

Postgres (Neon, in production) → PostgresSaver on a connection pool; SQLite (laptop, tests) → SqliteSaver.

Neon closes idle connections (and suspends the database after a few quiet minutes), so a single long-lived
connection died with "the connection is closed" on the first message after a pause (DEVLOG #50). The pool
checks each connection before handing it out and replaces dead ones.
Tables are created on first use (`setup()`), separate from our Alembic-managed tables.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import _normalise


def make_checkpointer(database_url: str):
    url = _normalise(database_url)
    if url.startswith("postgresql"):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from langgraph.checkpoint.postgres import PostgresSaver

        conninfo = url.replace("postgresql+psycopg://", "postgresql://", 1)
        pool = ConnectionPool(conninfo, min_size=1, max_size=5, max_idle=120, open=True,
                              check=ConnectionPool.check_connection,       # test before use; replace if closed
                              kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        saver = PostgresSaver(pool)
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
