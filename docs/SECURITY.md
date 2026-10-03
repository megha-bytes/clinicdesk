# Security

## Secrets
- **Production:** all secrets live in **Nebius SecretStash** (formerly MysteryBox; CLI/API identifier `mysterybox`). The backend VM's service account reads them at startup. Nothing secret is stored in code, Docker images, `.env` files or CI logs.
- **Development:** `.env.local` (git-ignored). `.env.example` lists variable names only.
- **Least privilege:** each component (API, onboarding, each Serverless Job) has its own service account and can read only the secrets it needs. Graph nodes never hold keys.
- **Never logged:** secrets are `pydantic.SecretStr`; the OpenTelemetry redaction processor strips auth headers and key-like attributes; secrets never enter LangGraph state or checkpoints.
- **Leak prevention:** gitleaks pre-commit hook and CI job; GitHub secret scanning enabled.

## A2A boundary
- One API key per calling agent (hashed at rest), per-key rate limits and spend caps.
- A2A callers get a narrower tool allow-list and pass through the same guardrails as patient messages.

## Data
- Hackathon demo uses **synthetic data only**. No real patient data is stored.
- Personal data is masked in logs, traces and staff event streams.

## Rotation
- Rotate every production key after the judging period ends (Dec 15, 2026):
  1. Create new key in the provider console.
  2. Add it as a new SecretStash version and make it primary.
  3. Restart services; confirm health.
  4. Revoke the old key.

## Reporting
Found a problem? Open a private security advisory on this repository.
