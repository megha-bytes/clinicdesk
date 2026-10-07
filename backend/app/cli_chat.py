"""Chat with ClinicDesk in the terminal (uses your Token Factory key from .env.local).

    cd backend
    py -m app.cli_chat

Seeds the demo clinic into the local database if it's empty. After each reply it shows the
models used, tokens, latency and cost for that turn. Type 'quit' to exit.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app import models as m
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.agent.tools import ToolContext
from app.db import Base, SessionLocal, engine
from app.graphs.clinicdesk import ask, build_graph
from app.seed import seed
from app.settings import get_settings


class TeeLedger(MemoryLedger):
    """Keeps records in memory for the per-turn summary and also writes them to the DB."""

    def __init__(self) -> None:
        super().__init__()
        from app.agent.models import DbLedger
        self.db = DbLedger()

    def record(self, rec) -> None:
        super().record(rec)
        self.db.record(rec)


def main() -> None:
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    s = get_settings()
    key = s.token_factory_api_key
    if key is None:
        sys.exit("TOKEN_FACTORY_API_KEY missing: add it to .env.local in the repo root.")
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        info = seed(session)
        clinic_id = info["clinic_id"]
        clinic = session.scalar(select(m.Clinic).where(m.Clinic.id == clinic_id))
        print(f"ClinicDesk · {clinic.name} (demo data). Type 'quit' to exit.\n")

    from openai import OpenAI
    ledger = TeeLedger()
    router = ModelRouter(OpenAI(base_url=s.token_factory_base_url, api_key=key.get_secret_value(), max_retries=1),
                         tiers=load_tiers(), ledger=ledger)
    session = SessionLocal()
    graph = build_graph(router, lambda: ToolContext(session=session, clinic_id=clinic_id))
    history: list[dict] = []
    try:
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                break
            if text.lower() in ("quit", "exit", "q"):
                break
            if not text:
                continue
            start = len(ledger.records)
            state = ask(graph, history, text)
            history = state["messages"]
            print(f"desk> {state['reply']}")
            turn = ledger.records[start:]
            tools = ", ".join(r["tool"] for r in state.get("tool_results", [])) or "none"
            cost = sum(r.cost_usd for r in turn)
            ms = sum(r.latency_ms for r in turn)
            calls = " · ".join(f"{r.tier} {r.latency_ms:.0f}ms" for r in turn)
            print(f"      [intent={state.get('intent')} lang={state.get('language')} tools={tools}]")
            print(f"      [{calls} | total {ms / 1000:.1f}s | ${cost:.5f}]\n")
    finally:
        session.close()


if __name__ == "__main__":
    main()
