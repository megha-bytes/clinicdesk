# ClinicDesk: a safe AI front desk for clinics

> An always-on clinic receptionist that books, confirms and reschedules appointments, registers patients, collects fees through payment links and runs the token queue, in English, Hindi and Kannada, by voice or chat. It never gives medical advice, every message passes through guardrails, staff see every step it takes, and any AI agent can book with the clinic over the open **A2A** protocol.

**The AI understands. The workflow decides.** NVIDIA Nemotron reads what the patient said; a LangGraph workflow decides what happens, and nothing is booked until the patient says yes.

Built for the **Nebius × NVIDIA Global AI Hackathon** (Best Apps and Agents track). [![CI](https://github.com/megha-bytes/clinicdesk/actions/workflows/ci.yml/badge.svg)](https://github.com/megha-bytes/clinicdesk/actions/workflows/ci.yml)

| | |
|---|---|
| **Live demo (judge page)** | https://clinicdesk-7foy.onrender.com |
| **Patient app** | https://clinicdesk-7foy.onrender.com/app |
| **Staff dashboard** | https://clinicdesk-7foy.onrender.com/staff (one-click demo roles, no password) |
| **A2A Agent Card** | https://clinicdesk-api.onrender.com/.well-known/agent-card.json |
| **API docs** | https://clinicdesk-api.onrender.com/docs |

The demo clinic uses **synthetic patients only** and is reset every night. The free backend may take
~30 s to wake on the first request.

## For judges: 2 minutes

1. Open the **judge page** and click a scenario: *Book in Hindi*, *Kannada emergency*, *Try to trick it*,
   *Ask for medicine*, *Call by voice*. The patient phone and the staff dashboard run side by side.
2. Switch the staff view between **Owner / Receptionist / Doctor** and see what each role may see.
3. Watch the **Agent X-ray**: the real LangGraph graph lights up node by node, with every Nemotron call,
   guardrail decision and its cost.
4. As **Owner**, paste a clinic website into *Add a clinic from its website*: Tavily reads it, Nemotron drafts
   the listing, and every value is marked *on the page* or *check this*.
5. Book over **A2A** with the Patient Companion (test key in the Devpost testing instructions).

## How we use NVIDIA and Nebius

| | Where | Why |
|---|---|---|
| **Nebius Token Factory** | every model call, at runtime (`app/agent/models.py`) | one OpenAI-compatible API and key for four Nemotron models; per-call cost ledger and a daily spend cap |
| **Nemotron 3.5 Lightning** | understands every patient message (intent + details, JSON) | fast, cheap, good at extraction in Hindi and Kannada; reasoning off |
| **Nemotron 3 Super** | writes non-booking replies; drafts clinic listings from Tavily pages | strong instruction following |
| **Nemotron 3 Ultra** | 2-line handoff notes for staff | quality over speed, off the hot path |
| **Nemotron 3 Nano** | the patient's own A2A companion agent | small, cheap, picks a slot from preferences |
| **NVIDIA NeMo Guardrails** | input and output rails as graph nodes (`app/guardrails/`) | emergencies, injection, payment secrets, privacy, medical advice, invented facts |
| **Tavily** | clinic onboarding, owner side only (`app/onboarding/`) | reads a clinic's site so the owner reviews a draft instead of typing it |

## Built with
- **NVIDIA Nemotron** (Lightning, Nano, Super, Ultra) on **Nebius Token Factory**; **NVIDIA NeMo Guardrails**
- **LangGraph** (checkpointed in Postgres, interrupts for consent and confirmation), exposed over **A2A**, streamed to the UI with **AG-UI**
- **FastAPI**, **React + Vite + Tailwind**, **OpenTelemetry** tracing with per-turn cost, **Tavily**
- Hosting: Render (free) + Neon Postgres; secrets in the host's secret store behind a `SecretsProvider` (Nebius SecretStash-ready)
- **487 backend tests** (SQLite + Postgres), 13 frontend tests, 61 red-team conversations in CI

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

React + Vite + TypeScript + Tailwind in `frontend/`: `/` judge page, `/app` patient app, `/staff` dashboard. Chat with live status lines, tappable times, consent and
confirmation cards, a printed-slip booking confirmation with a pay button, a full-screen emergency screen
with tap-to-call, and English / हिन्दी / ಕನ್ನಡ. Built on the AG-UI stream, with one conversation engine
(`useConversation`) shared by the chat and the voice call screen.

Run it locally (two terminals):
```bash
# 1. backend (from backend/)
python -m app.seed                 # load the demo clinic into the local database (once)
python -m uvicorn app.main:app --reload

# 2. app (from frontend/)
npm install
npm run dev                        # http://localhost:5173
```
Live: `https://clinicdesk-7foy.onrender.com` (Render static site, see `deploy/render.yaml`).

## Voice: call the clinic

Phone calls are how most patients reach a clinic, so the patient app opens with **Call the clinic**.
The call screen talks and listens in English, Hindi and Kannada, using the same LangGraph workflow,
guardrails and cards as the chat:

- **Listening**: the browser's speech recognition (`en-IN`, `hi-IN`, `kn-IN`; Chrome, Edge, Safari).
  Spoken numbers become digits on the server (`app/voice/spoken.py`): "nine eight four five…",
  "double seven", "दो नंबर", Devanagari and Kannada digits. "Yes please" / "haan ji" / "ಹೌದು" answers a card.
- **Speaking**: every reply has a `speech` version written for the ear (`app/voice/speech.py`):
  "tomorrow, 9:30 AM", "कल, सुबह 9:30", "500 ರೂಪಾಯಿ", "Option 1: …", "Say yes or no." No emoji, no links.
- **Turn-taking**: greet → listen → think (live status) → speak → listen; tap the mic to interrupt.
- **Tagged**: requests carry `channel: "voice"`, so bookings show a phone icon on the staff dashboard.
- **Privacy**: in Chrome, speech recognition is done by Google's speech service; the screen says so and
  tells callers never to say card numbers or OTPs (the payment guardrail also catches spoken card numbers).
- **Swappable**: speech sits behind a small `SpeechEngine` interface (`frontend/src/voice/engine.ts`),
  ready for a server-side provider. Kannada voices vary by device; without one, replies are shown on screen.

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

## Staff dashboard and Agent X-ray

`/staff` on the app: today's schedule per doctor (bookings appear live, tagged when the assistant, a phone
call or another agent made them), check-in / start / finish / no-show, emergency alerts with sound, handoff
requests, and the **Agent X-ray**: the real LangGraph workflow drawn from the compiled graph, lighting up step
by step during a conversation, with every model call (tier, latency, cost), tool and guardrail decision.
"Today" shows bookings, model cost per booking, typical latency, guardrail decisions and the red-team result.
Phone numbers are never shown; one-click demo sessions also shorten patient names.

## Security: staff logins, roles and who owns a conversation

- **Staff accounts** with scrypt-hashed passwords; random bearer session tokens stored only as SHA-256 hashes
  (revocable; 12 h absolute, 2 h idle); 5 wrong passwords lock the account for 15 minutes; the same error for an
  unknown email and a wrong password; per-IP login rate limit; logins audited.
- **Roles, always scoped to the staff member's own clinic:**

  | | Owner | Receptionist | Doctor |
  |---|---|---|---|
  | Schedule, queue, alerts | all doctors | all doctors | own column only |
  | Check-in / no-show | ✅ | ✅ | — |
  | Start / finish a visit | ✅ | — | own patients |
  | Agent X-ray, model cost, red-team results | ✅ | — | — |
  | Clinic onboarding (Tavily) | ✅ | — | — |

  The live feed filters events per role (no cost for non-owners; doctors only see their own bookings). It uses
  one-time 60-second tickets, so a login token never appears in a URL.
- **Patients own their conversations:** the first reply carries a secret thread key; later calls must send it
  (`X-Thread-Key`). A guessed or leaked `thread_id` alone returns 404. Patients need no account.
- **Demo:** with `DEMO_LOGIN_ENABLED` the public demo clinic offers one-click Owner / Receptionist / Doctor
  sessions (the hackathon asks for a demo without credentials); demo sessions can't manage staff accounts.
- Security headers on the API (CSP, HSTS, nosniff, frame-deny); secrets only in `.env.local` / the host /
  GitHub secrets; `gitleaks` in pre-commit and CI.
- Create real accounts: `cd backend && py -m app.auth create --email you@clinic.in --name "Dr. Mehta" --role owner`
  (the password is typed, never passed as an argument), or set `OWNER_EMAIL` + `OWNER_PASSWORD` on the host.

## Clinic onboarding with Tavily

Owners paste the clinic's website (or its name and area). **Tavily** Extract/Search reads the pages,
**Nemotron Super** turns them into a draft listing (doctors, specialties, hours, fees, address, phone), and a
deterministic check marks every value *on the page* or *check this* (for example a fee the page doesn't say).
Nothing goes live from a draft; the owner approves or rejects it (audited). Tavily is never used in the
patient chat, where web results could turn into medical advice.

## A2A: the clinic as an agent

Other AI agents can discover the clinic and book for a patient over the open **A2A** protocol (`a2a-sdk` 1.2).

- **Agent Card:** `/.well-known/agent-card.json` with 5 skills (book, check availability, reschedule/cancel,
  queue status, clinic info) and API-key auth (`X-API-Key`).
- **JSON-RPC endpoint:** `/a2a`, with streaming status lines ("Checking Dr. Rao's calendar…").
- **Same safety as patients:** the same LangGraph workflow, guardrails, consent and confirmation. Consent and
  confirmation cards come back as task state `input-required`, so the calling agent has to ask the patient.
  Emergencies complete immediately with `metadata.escalated = true`. A booking returns a JSON artifact.
- Bookings are tagged `a2a` with the calling agent's id (network icon on the staff dashboard); per-caller rate
  limits; keys are stored as hashes (`py -m app.a2a.auth create "Name"`), or set `A2A_DEMO_KEY` on Render.

**Patient Companion** (`companion/companion.py`): a personal-assistant agent that reads the Agent Card, writes
the request with the patient's saved details, picks the slot that fits their preferences with Nemotron Nano,
and always asks the human before answering consent or confirmation.
```bash
py companion/companion.py --clinic http://localhost:8000 --key cdk_... "Book Dr. Rao tomorrow morning"
```

## Red-team suite

61 scripted attack and edge-case conversations in `backend/redteam/cases.yaml` across 10 categories:
emergencies (3 languages, mid-booking, model down), prompt injection (incl. Hindi, inside a name), other
patients' data, medical advice, identity (acting for someone else), consent and confirmation, invented facts,
payment secrets (card numbers, OTPs, PINs), limits and language stability.

- **Every push (CI):** the cases run through the real graph, guardrails, tools and database with a scripted
  model that behaves adversarially where the case says so (`pytest tests/test_redteam.py`).
- **Nightly (GitHub Actions):** the same conversations against the deployed app with the real Nemotron models
  (`scripts/redteam.py --live <url>`), report uploaded as an artifact.
- Latest scripted report: [`docs/redteam-scripted.md`](docs/redteam-scripted.md).

## Real-time layer

Every graph step streams live, so the patient sees what the agent is doing and staff see bookings arrive.

| Endpoint | Who | Format |
|---|---|---|
| `POST /chat/stream`, `POST /chat/answer/stream` | patient app | **AG-UI** events over SSE: `RUN_STARTED`, `STEP_STARTED/FINISHED` (graph nodes), `TOOL_CALL_START/END`, `TEXT_MESSAGE_*` (reply), `CUSTOM` (status line, confirmation card, guardrail, emergency, booking, model call), `RUN_FINISHED` |
| `GET /chat/{thread_id}/events?after=N` | patient app (reconnect) | replay + live, SSE with `id:` cursors (`Last-Event-ID` supported) |
| `WS /ops/ws?ticket=…&after=N`, `GET /ops/recent` | staff dashboard (login) | JSON events, PII-free (no patient names, phones or replies), filtered by role |

Try it: `curl -N -X POST https://clinicdesk-api.onrender.com/chat/stream -H "content-type: application/json" -d "{\"text\": \"Dr Rao tomorrow\"}"`

## Try the live agent

Open `https://clinicdesk-api.onrender.com/docs`:
1. **POST /chat/turn** with `{"text": "What are Dr. Rao's timings?"}`.
2. For a booking, keep the returned `thread_id` and `thread_key`, and send the key as the `X-Thread-Key` header
   on later calls. When the response contains an `interrupt` (consent or a booking confirmation), answer it with
   **POST /chat/answer** `{"thread_id": "...", "approved": true}`.

The assistant can't register or book anything without those explicit answers; the graph enforces it.

## Licence
[Apache-2.0](LICENSE)
