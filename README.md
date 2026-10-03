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
```bash
cp .env.example .env.local        # fill in your own keys; never commit them
pip install pre-commit && pre-commit install   # gitleaks secret scanning on every commit
```
Full setup instructions will be added as components land.

## Licence
[Apache-2.0](LICENSE)
