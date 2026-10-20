"""The Postgres checkpointer survives the database closing its connections (Neon idles them out, DEVLOG #50)."""
import os

import pytest

from app.graphs.checkpointer import make_checkpointer

PG = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not PG.startswith("postgres"), reason="needs TEST_DATABASE_URL (Postgres)")


def test_checkpointer_recovers_after_connections_are_killed():
    import psycopg
    saver = make_checkpointer(PG)
    cfg = {"configurable": {"thread_id": "t-recover", "checkpoint_ns": ""}}
    assert saver.get_tuple(cfg) is None
    with psycopg.connect(PG.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as admin:
        admin.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                      "where datname = current_database() and pid <> pg_backend_pid()")
    assert saver.get_tuple(cfg) is None                     # used to raise "the connection is closed"
    saver.conn.close()
