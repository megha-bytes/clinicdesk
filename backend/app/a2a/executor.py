"""Runs A2A tasks through the same ClinicDesk workflow as patients (guardrails, consent, confirmation).

- context_id → one conversation thread (`a2a-<context_id>`); bookings are tagged source "a2a" + the caller.
- Consent and confirmation cards → task state input-required (the calling agent must ask the patient).
- Status lines stream as working updates; bookings come back as a JSON artifact; emergencies complete
  immediately with metadata escalated=true.
"""
from __future__ import annotations

import asyncio
import contextvars
import json

from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState
from google.protobuf import json_format, struct_pb2
from sqlalchemy import select

from app import models as m
from app.a2a import auth
from app.agent.tools import ToolContext
from app.db import SessionLocal

CALLER: contextvars.ContextVar[str | None] = contextvars.ContextVar("a2a_caller", default=None)


def _make_ctx() -> ToolContext:
    from app.chat_api import _demo_clinic_id
    session = SessionLocal()
    try:
        return ToolContext(session=session, clinic_id=_demo_clinic_id(session), owns_session=True,
                           channel="a2a", a2a_caller_id=CALLER.get())
    except Exception:
        session.close()
        raise


class A2ARuntime:
    graph = None

    @classmethod
    def get(cls):
        if cls.graph is None:
            from app.chat_api import _Runtime
            from app.graphs.clinicdesk import build_graph
            router, checkpointer = _Runtime.parts()
            cls.graph = build_graph(router, _make_ctx, checkpointer)
        return cls.graph


def _conversation(thread_id: str, context_id: str, caller_id: str) -> str:
    from app.chat_api import _demo_clinic_id
    with SessionLocal() as s:
        conv = s.scalar(select(m.Conversation).where(m.Conversation.thread_id == thread_id))
        if conv is None:
            conv = m.Conversation(clinic_id=_demo_clinic_id(s), channel="a2a", thread_id=thread_id,
                                  a2a_context_id=context_id)
            s.add(conv)
            s.commit()
        return conv.id


def _data_part(obj: dict) -> Part:
    v = struct_pb2.Value()
    json_format.ParseDict(json.loads(json.dumps(obj, default=str)), v)
    return Part(data=v, media_type="application/json")


def _message(text: str, task, data: dict | None = None, meta: dict | None = None):
    msg = new_text_message(text, context_id=task.context_id, task_id=task.id)
    if data:
        msg.parts.append(_data_part(data))
    if meta:
        msg.metadata.update(meta)
    return msg


class ClinicDeskExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue, task.id, task.context_id)

        headers = context.call_context.state.get("headers", {}) if context.call_context else {}
        caller = await asyncio.to_thread(auth.authenticate, headers.get("x-api-key"))
        if caller is None:
            await up.reject(_message("Missing or invalid API key. Ask the clinic for an X-API-Key.", task))
            return
        if not auth.rate_ok(caller["id"], caller["rate_limit_per_min"]):
            await up.reject(_message("Too many requests. Wait a minute and try again.", task))
            return
        from app.chat_api import demo_clinic_id, spend_ok
        if not await asyncio.to_thread(spend_ok):
            await up.reject(_message("The clinic's assistant has reached today's usage limit.", task))
            return
        text = (context.get_user_input() or "").strip()[:1000]
        if not text:
            await up.requires_input(_message("Send the request as a text message.", task))
            return

        from app.graphs.clinicdesk import pending_interrupt
        from app.realtime import runner
        graph = A2ARuntime.get()
        thread_id = f"a2a-{task.context_id}"
        conv_id = await asyncio.to_thread(_conversation, thread_id, task.context_id, caller["id"])
        clinic_id = await asyncio.to_thread(demo_clinic_id)
        waiting = await asyncio.to_thread(pending_interrupt, graph, thread_id)

        loop = asyncio.get_running_loop()
        statuses: asyncio.Queue = asyncio.Queue()

        def on_event(ev: dict) -> None:
            if ev["type"] == "status":
                loop.call_soon_threadsafe(statuses.put_nowait, ev["payload"].get("text", ""))

        def run():
            if waiting:
                return runner.run(graph, thread_id, approved=text, clinic_id=clinic_id, on_event=on_event)
            return runner.run(graph, thread_id, text=text, conversation_id=conv_id, clinic_id=clinic_id,
                              on_event=on_event)

        token = CALLER.set(caller["id"])
        try:
            job = asyncio.ensure_future(asyncio.to_thread(run))   # to_thread copies CALLER into the worker
        finally:
            CALLER.reset(token)
        await up.start_work()
        while not job.done():
            try:
                line = await asyncio.wait_for(statuses.get(), 0.2)
                await up.update_status(TaskState.TASK_STATE_WORKING, _message(line, task))
            except asyncio.TimeoutError:
                pass
        try:
            out = job.result()
        except Exception:  # noqa: BLE001
            await up.failed(_message("Something went wrong. The clinic staff can help: call the clinic.", task))
            return
        await self._finish(up, task, out)

    async def _finish(self, up: TaskUpdater, task, out: dict) -> None:
        ui = out.get("ui") or {}
        card = out.get("interrupt")
        data = {"language": out.get("language")}
        if card:
            data["card"] = {k: card.get(k) for k in ("kind", "action", "summary", "details")}
            await up.requires_input(_message(f"{card['summary']}\nAsk the patient, then reply yes or no.", task, data))
            return
        reply = out.get("reply") or ""
        if ui.get("emergency"):
            await up.complete(_message(reply, task, {**data, "emergency": ui["emergency"]},
                                       meta={"escalated": True, "emergency": ui["emergency"].get("kind")}))
            return
        if ui.get("booked"):
            await up.add_artifact([_data_part(ui["booked"])], name="booking")
            await up.complete(_message(reply, task, {**data, "booked": ui["booked"]}))
            return
        if ui.get("options"):
            data["options"] = ui["options"]
        if ui.get("refused"):
            data["refused"] = ui["refused"]
        if ui.get("options") or ui.get("ask") or ui.get("chosen"):
            data["needs"] = ui.get("ask") or ["option"]
            await up.requires_input(_message(reply, task, data))
            return
        await up.complete(_message(reply, task, data))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
