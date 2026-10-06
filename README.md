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
docs/        security, architecture, feedback log
```

## Safety & privacy
- Administrative tasks only. **Not a medical device**; no medical advice, diagnosis or triage.
- Emergencies are screened before every reply and redirected to **108 / 112** (and **Tele-MANAS 14416**).
- The demo uses **synthetic data only**.

See [docs/SECURITY.md](docs/SECURITY.md).

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
```

**Full local stack with Docker** (Postgres, OpenTelemetry Collector, Jaeger):
```bash
docker compose -f deploy/docker-compose.yml up --build
# API http://localhost:8000/health · Jaeger http://localhost:16686
# Visit http://localhost:8000/debug/trace, then find the span in Jaeger (phone numbers are redacted)
```

## Licence
[Apache-2.0](LICENSE)
