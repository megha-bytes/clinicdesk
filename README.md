# ClinicDesk: a safe AI front desk for clinics

> An always-on clinic receptionist that books, confirms and reschedules appointments, registers patients, collects fees through payment links and runs the token queue, in English, Hindi and Kannada. It never gives medical advice, every action passes through guardrails, and any AI agent can book with the clinic over the open **A2A** protocol.

Built for the **Nebius × NVIDIA Global AI Hackathon** (Best Apps and Agents track).

🚧 **Status:** in active development (Oct 2026).

| | |
|---|---|
| **Demo** | _coming soon_ |
| **Video** | _coming soon_ |
| **A2A Agent Card** | _coming soon_ |

## Built with
- **NVIDIA Nemotron 3** Nano / Super / Ultra on **Nebius Token Factory**
- **NVIDIA NeMo Guardrails**
- **LangGraph** workflows, exposed over **A2A**, streamed to the UI with **AG-UI**
- **OpenTelemetry** tracing with per-turn cost
- **Nebius SecretStash** and **Serverless Jobs**
- **Tavily** for clinic onboarding

## Repository layout
```
backend/     FastAPI app: LangGraph graphs, guardrails, engine, real-time gateway, A2A server
companion/   "Patient Companion" demo A2A client
config/      model tiers and pricing
frontend/    React PWA: patient app and staff dashboard
deploy/      docker-compose, OTel collector, Nebius deployment
docs/        security, architecture, dev log
```

## Safety & privacy
- Administrative tasks only. **Not a medical device**; no medical advice, diagnosis or triage.
- Emergencies are screened before every reply and redirected to **108 / 112** (and **Tele-MANAS 14416**).
- The demo uses **synthetic data only**.

See [docs/SECURITY.md](docs/SECURITY.md). Build notes, problems and test results: [docs/DEVLOG.md](docs/DEVLOG.md).

## Development setup

**Prerequisites:** Python 3.12+, Git. Docker Desktop is optional (for Postgres and Jaeger traces locally).

```bash
# 1. Your dev key (never committed: .env.local is git-ignored)
cp .env.example .env.local          # then set TOKEN_FACTORY_API_KEY=...

# 2. Secret scanning on every commit
pip install pre-commit && pre-commit install

# 3. Check Token Factory + Nemotron (tiers, tool calling, Hindi, Kannada)
pip install openai python-dotenv pyyaml
python scripts/spike.py

# 4. Run the API (SQLite by default)
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload       # http://localhost:8000/health, docs at /docs

# 5. Tests
pytest

# 6. Chat with the agent in the terminal (demo clinic, real Nemotron models;
#    consent and booking confirmations appear as yes/no questions)
python -m app.cli_chat

# 7. Which reasoning switch works on Token Factory + Hindi/Kannada latency per model
cd .. && python scripts/check_reasoning.py
```

**Full local stack with Docker** (Postgres, OpenTelemetry Collector, Jaeger):
```bash
docker compose -f deploy/docker-compose.yml up --build
# API http://localhost:8000/health · Jaeger http://localhost:16686
# Visit http://localhost:8000/debug/trace, then find the span in Jaeger (phone numbers are redacted)
```

## Patient app (frontend)

React + Vite + TypeScript + Tailwind in `frontend/`. Chat with live status lines, tappable times, consent and
confirmation cards, a printed-slip booking confirmation with a pay button, a full-screen emergency screen
with tap-to-call, and English / हिन्दी / ಕನ್ನಡ. Built on the AG-UI stream, with one conversation engine
(`useConversation`) that the voice call screen will reuse.

Run it locally (two terminals):
```bash
# 1. backend (from backend/)
python -m app.seed                 # load the demo clinic into the local database (once)
python -m uvicorn app.main:app --reload

# 2. app (from frontend/)
npm install
npm run dev                        # http://localhost:5173
```
Live: `https://clinicdesk-app.onrender.com` (Render static site, see `deploy/render.yaml`).

## Guardrails

Every patient message passes **input rails** before anything else runs, and every model-written reply
passes an **output rail**. The policy is defined in NVIDIA NeMo Guardrails (Colang, `backend/app/guardrails/config/`);
the checks are deterministic actions, so they work even when the model provider is down.

| Rail | Catches | What the patient gets |
|---|---|---|
| Emergency (en/hi/kn) | chest pain, breathing trouble, stroke signs, unconsciousness, heavy bleeding, poisoning, seizures, self-harm | 108 / 112 (Tele-MANAS 14416 for self-harm) + staff alerted; "not urgent" resumes the booking |
| Prompt injection | "ignore previous instructions", fake system tags, "developer mode"… | "I can only help with appointments at this clinic." |
| Other patients | lists of patients, someone else's details or appointments | "Sorry, I can't share other patients' details." |
| Medical advice | which medicine / dose, "is it serious", diagnosis | refusal + offer to book (booking continues if one is in progress) |
| Output facts | medical advice, phone numbers, fees or times not in the tool results | reply rewritten once, then a safe fallback |

Asking for a person hands over to staff with a 2-line note written by Nemotron Ultra. Every decision is
logged in `guardrail_event` (no patient text). `/health` shows which engine is running (`nemo-guardrails`
in the container; on Python 3.14, where NeMo isn't available yet, the same checks run directly).

## Real-time layer

Every graph step streams live, so the patient sees what the agent is doing and staff see bookings arrive.

| Endpoint | Who | Format |
|---|---|---|
| `POST /chat/stream`, `POST /chat/answer/stream` | patient app | **AG-UI** events over SSE: `RUN_STARTED`, `STEP_STARTED/FINISHED` (graph nodes), `TOOL_CALL_START/END`, `TEXT_MESSAGE_*` (reply), `CUSTOM` (status line, confirmation card, guardrail, emergency, booking, model call), `RUN_FINISHED` |
| `GET /chat/{thread_id}/events?after=N` | patient app (reconnect) | replay + live, SSE with `id:` cursors (`Last-Event-ID` supported) |
| `WS /ops/ws?after=N`, `GET /ops/recent` | staff dashboard | JSON events, PII-free (no patient names, phones or replies); optional `STAFF_DASHBOARD_TOKEN` |

Try it: `curl -N -X POST https://clinicdesk-api.onrender.com/chat/stream -H "content-type: application/json" -d "{\"text\": \"Dr Rao tomorrow\"}"`

## Try the live agent

Open `https://clinicdesk-api.onrender.com/docs`:
1. **POST /chat/turn** with `{"text": "What are Dr. Rao's timings?"}`.
2. For a booking, keep the returned `thread_id`. When the response contains an `interrupt` (consent or a
   booking confirmation), answer it with **POST /chat/answer** `{"thread_id": "...", "approved": true}`.

The assistant can't register or book anything without those explicit answers; the graph enforces it.

## Licence
[Apache-2.0](LICENSE)
