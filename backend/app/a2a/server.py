"""Mount the A2A server on the FastAPI app: Agent Card + JSON-RPC endpoint at /a2a."""
from __future__ import annotations

from fastapi import FastAPI


def mount_a2a(app: FastAPI) -> None:
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore

    from app.a2a.card import agent_card
    from app.a2a.executor import ClinicDeskExecutor

    card = agent_card()
    handler = DefaultRequestHandler(agent_executor=ClinicDeskExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card, cache_control="public, max-age=300"),
                              jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"))
