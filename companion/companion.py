"""Patient Companion: a personal assistant agent that books at ClinicDesk over A2A.

    py companion\\companion.py --clinic http://localhost:8000 --key cdk_...        (from the repo folder)

It reads the clinic's Agent Card, writes the request with your saved details (companion/profile.json, or
profile.example.json), answers the clinic's questions about name/phone/age from the profile, and picks the
time that best fits your preferences using Nemotron Nano (Token Factory key from .env.local). If no key is
set, it falls back to simple rules.

Safety: the companion never answers a consent or booking confirmation by itself. It shows you the clinic's
exact question and sends your yes/no.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf import json_format

ROOT = Path(__file__).resolve().parents[1]
NANO = os.environ.get("COMPANION_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
BASE_URL = os.environ.get("TOKEN_FACTORY_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")


def load_profile() -> dict:
    for name in ("profile.json", "profile.example.json"):
        f = Path(__file__).with_name(name)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    raise SystemExit("No companion/profile.json")


def model_client():
    key = os.environ.get("TOKEN_FACTORY_API_KEY")
    if not key and (ROOT / ".env.local").exists():
        for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
            if line.startswith("TOKEN_FACTORY_API_KEY="):
                key = line.split("=", 1)[1].split("#", 1)[0].strip()
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=key, max_retries=1)


def nano(client, system: str, user: str, max_tokens: int = 120) -> str | None:
    if client is None:
        return None
    try:
        r = client.chat.completions.create(model=NANO, temperature=0, max_tokens=max_tokens,
                                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                           extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        text = (r.choices[0].message.content or "").strip()
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"   (Nemotron Nano unavailable: {type(e).__name__}; using simple rules)")
        return None


def first_message(goal: str, p: dict, client) -> str:
    out = nano(client, "You are a patient's personal assistant writing ONE short message to a clinic's booking agent. "
                       "Include the request and the patient's name, age and 10-digit phone exactly as given. "
                       "No medical details beyond the reason given. Plain text, one or two sentences.",
               f"Request: {goal}\nPatient: {p['name']}, {p['age']} years, phone {p['phone']}")
    return out or f"{goal}, for {p['name']} ({p['age']} years), mobile {p['phone']}."


def pick_option(options: list[dict], p: dict, client) -> int:
    for o in options:                         # A2A data parts carry numbers as floats (1.0)
        o["n"] = int(o["n"])
    listing = "\n".join(f"{o['n']}. {o.get('doctor') or ''} {o['when']}" for o in options)
    out = nano(client, "Pick the option that best fits the patient's preferences. Reply with the number only.",
               f"Preferences: {p.get('preferences', '')}\nOptions:\n{listing}", max_tokens=5)
    m = re.search(r"\d", out or "")
    if m and 1 <= int(m.group()) <= len(options):
        return int(m.group())
    if "morning" in p.get("preferences", "").lower():                 # simple rule fallback
        am = [o for o in options if "AM" in o["when"]]
        if am:
            return am[0]["n"]
    return options[0]["n"]


def details_reply(needs: list[str], p: dict, goal: str) -> str:
    parts = [goal] if "doctor" in needs else []
    if "name" in needs:
        parts.append(f"Name {p['name']}")
    if "phone" in needs:
        parts.append(f"mobile {p['phone']}")
    if "age" in needs:
        parts.append(f"age {p['age']} years")
    return ", ".join(parts) or f"{p['name']}, {p['phone']}, {p['age']} years"


def data_of(msg) -> dict:
    for part in msg.parts:
        if part.HasField("data"):
            return json_format.MessageToDict(part.data)
    return {}


def text_of(msg) -> str:
    return "\n".join(part.text for part in msg.parts if part.text)


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:                          # no human at the keyboard: never agree on their behalf
        print()
        return ""


async def converse(clinic: str, key: str, goal: str, ask=_ask) -> dict | None:
    p = load_profile()
    llm = model_client()
    hx = httpx.AsyncClient(headers={"x-api-key": key}, timeout=60)
    card = (await hx.get(f"{clinic.rstrip('/')}/.well-known/agent-card.json")).json()
    print(f"\n🤝 Found {card['name']}")
    print("   Skills: " + ", ".join(s["name"] for s in card["skills"]))
    client = await create_client(clinic, client_config=ClientConfig(httpx_client=hx, streaming=True))
    task_id = context_id = None
    text = first_message(goal, p, llm)
    last_reply = None
    for _ in range(12):
        print(f"\n🧑‍💼 companion → clinic: {text}")
        msg = Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)])
        if context_id:
            msg.context_id = context_id
        if task_id:
            msg.task_id = task_id
        tid = cid = None
        status = None
        artifacts = []
        async for ev in client.send_message(SendMessageRequest(message=msg)):   # streamed: task, then updates
            if ev.HasField("task"):
                tid, cid, status = ev.task.id, ev.task.context_id, ev.task.status
                artifacts = list(ev.task.artifacts) or artifacts
            elif ev.HasField("status_update"):
                tid, cid, status = ev.status_update.task_id, ev.status_update.context_id, ev.status_update.status
                if status.state == TaskState.TASK_STATE_WORKING and text_of(status.message):
                    print(f"   · {text_of(status.message)}")
            elif ev.HasField("artifact_update"):
                artifacts.append(ev.artifact_update.artifact)
        if status is None:
            print("   (no answer)")
            return None
        context_id = cid
        state = status.state
        reply, data = text_of(status.message), data_of(status.message)
        print(f"🏥 clinic: {reply}")
        if state == TaskState.TASK_STATE_COMPLETED:
            task_id = None
            meta = json_format.MessageToDict(status.message.metadata)
            if meta.get("escalated"):
                print("\n🚨 The clinic flagged an emergency. Follow its instructions now.")
            booking = json_format.MessageToDict(artifacts[0].parts[0].data) if artifacts else None
            if booking:
                print(f"\n✅ Booked {booking['doctor']}, {booking['when']}, ₹{int(booking['fee_inr'])}. Pay: {booking.get('pay_url')}")
            return booking
        if state != TaskState.TASK_STATE_INPUT_REQUIRED:
            print(f"   (task ended: {TaskState.Name(state)})")
            return None
        task_id = tid
        needs = [n for n in (data.get("needs") or []) if n != "option"]
        if data.get("card"):                                            # consent / confirmation → the human decides
            yn = ask(f"\n❓ The clinic asks: {data['card']['summary']}\n   Your answer (yes/no): ").strip() or "no"
            text = yn
        elif reply == last_reply:                                      # same question twice: ask the human
            text = ask(f"\n❓ The clinic keeps asking: {reply}\n   Your reply: ").strip() or "Please ask the staff to call me."
        elif data.get("options"):
            n = pick_option(data["options"], p, llm)
            chosen = next(o for o in data["options"] if o["n"] == n)
            print(f"   🧠 picked option {n} ({chosen['when']}) to fit: {p.get('preferences', '')}")
            text = f"{n}, {details_reply(needs, p, goal)}" if needs else str(n)
        else:
            text = details_reply(data.get("needs") or [], p, goal)
        last_reply = reply
    print("   (stopped after 12 turns)")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clinic", default="http://localhost:8000")
    ap.add_argument("--key", default=os.environ.get("A2A_KEY"), help="X-API-Key issued by the clinic")
    ap.add_argument("goal", nargs="*", help='e.g. "Book Dr. Rao tomorrow morning"')
    a = ap.parse_args()
    if not a.key:
        sys.exit("Pass --key (create one with: cd backend && py -m app.a2a.auth create \"My companion\")")
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    goal = " ".join(a.goal) or input("What should I book? ")
    asyncio.run(converse(a.clinic, a.key, goal))


if __name__ == "__main__":
    main()
