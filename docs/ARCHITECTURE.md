# Architecture

_To be filled in as components land. Planned sections:_

1. **LangGraph workflows:** `ClinicDeskGraph` (guard_input → identify → intent → subgraphs → respond → guard_output), plus Onboarding, Reminder, Waitlist and Insights graphs; Postgres checkpointer; interrupts for consent and confirmation.
2. **Guardrails:** NeMo Guardrails rails as graph nodes, with deterministic actions for emergencies and verified facts.
3. **Model tiers:** Nemotron Nano / Super / Ultra on Nebius Token Factory.
4. **Real-time layer:** one `ExecutionEvent` stream → AG-UI (patient app), A2A (other agents), OpenTelemetry (traces and cost).
5. **A2A:** Agent Card, skills, LangGraph ↔ A2A mapping.
6. **Data model** and **secrets**.
