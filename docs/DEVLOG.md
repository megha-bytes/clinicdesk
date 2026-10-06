# ClinicDesk dev log

Internal log of problems, decisions and test results, appended as we build.
Newest entries at the bottom. The **Open issues** table is the checklist to review before submission: every row should end up ✅ Resolved or ⏸ Accepted (with a reason).

## Open issues

| # | Issue | Found | Status | Resolution / next step |
|---|---|---|---|---|
| 1 | Models invent facts (hours, slots, phone numbers, websites) when they have no tool data | Oct 6 | 🔴 Open | Build the `verified_facts` output rail (Day 10); replies may only state values returned by tools. Re-run the spike prompts through the full graph to confirm |
| 2 | Lightning leaks its reasoning ("Here's a thinking process…") into plain-text replies and hits the token cap | Oct 6 | 🔴 Open | Model router (Day 6): switch reasoning off for Lightning, and strip any thinking text before it reaches a patient. Add a test |
| 3 | Hindi/Kannada replies via Super took ~28–30 s (English ~2.8 s) | Oct 6 | 🔴 Open | Re-measure in the router with reasoning off; try Ultra/Lightning for Indic replies. Target: first token < 1.5 s, full reply < 4 s p95 |
| 4 | Kannada reply mixed in English ("ಮಿತ Places") | Oct 6 | 🟡 Watch | Prompt: "reply only in Kannada script"; add a language check to the output rail; native-speaker check before the video |
| 5 | Super calls tools one at a time (3 rounds), so naive single-round code returns an empty reply | Oct 6 | ✅ Resolved | Spike loops until a final reply; the agent graph must do the same (tool loop with max rounds) |
| 6 | Nemotron Nano Omni and a Nemotron safety-guard model are not on Token Factory | Oct 6 | ⏸ Accepted | Dropped fee-board photo onboarding; content-safety rail uses NeMo's self-check prompt on the fast tier |
| 7 | Nebius AI Cloud VM (~$0.05/h ≈ $36/month) exceeds credits for a demo that must stay up until Dec 15 | Oct 3 | ⏸ Accepted | Local dev + free hosting (Render/Fly + Neon + Vercel); Nebius credits go to Token Factory. SecretStash/Serverless Jobs kept as upgrade path |
| 8 | Builders Program application "under review" (extra $25 Token Factory + Tavily credits pending) | Oct 3 | 🟡 Waiting | Ask in Nebius Discord if no reply by Oct 7. Tavily isn't needed until Day 18 (Oct 21) |
| 9 | Token Factory header shows "Trial: $1.00 · 27 days"; promo credit should total $29 | Oct 6 | 🟡 Check | Confirm in Billing that the promo credit is on the same account and project |
| 10 | First deploy (Render + Neon) not done yet | Oct 6 | 🔴 Open | Day 2 leftover; do before the frontend work (Day 12) |

Status key: 🔴 Open · 🟡 Watch/Waiting · ✅ Resolved · ⏸ Accepted

---

## Log

### 2026-10-03 · Setup

**Token Factory promo credit didn't seem to apply**
- Symptom: after sign-up the balance showed "$1 trial credits".
- Cause: the $1 is the automatic 30-day trial; the hackathon promo (`NEBIUS-DEVPOST-GLOBAL26`) is applied separately under Balance → Top up → With promo code (a bank card is required first).
- Result: balance $29 after applying. ✅

**Builders Program**
- Sign-up submitted; page says the application is under review. No timeline given. → Issue #8.

**GitHub push failed: "Repository not found"**
- Symptom: `git push -u origin main` → `remote: Repository not found.`
- Cause: `git remote add` only saves the URL; the repo didn't exist on GitHub yet.
- Fix: created the empty public repo `megha-bytes/clinicdesk` on github.com (no README/licence), pushed again. ✅

**`pip` not recognised on Windows**
- Symptom: `'pip' is not recognized as an internal or external command`.
- Fix: use `py -m pip …` and `py -m pre_commit …`. ✅
- Output: all pre-commit hooks passed, including gitleaks ("Detect hardcoded secrets … Passed").

**GitHub security**
- Secret scanning and push protection were already enabled by default on the public repo. ✅

**Nebius AI Cloud hosting decision**
- Pricing page: Non-GPU Intel Ice Lake CPU $0.012/vCPU-h; RAM ~$0.0032/GiB-h → 2 vCPU / 8 GiB ≈ $0.05/h ≈ $36/month. Adding a card triggers a $25 prepaid charge.
- Demo must stay live through judging (Dec 1–15) → ~$63 from Oct 23. Exceeds credits.
- Decision: local dev + free hosting; no Nebius card. → Issue #7.

### 2026-10-06 · Day 1–2 code

**Backend skeleton built and tested**
- FastAPI `/health`, settings, `SecretsProvider` (env + SecretStash stub), all data-model tables, Alembic initial migration, PII-redacting OpenTelemetry exporter, Docker compose, Render blueprint, CI.
- Tests: 18 passed on SQLite and on Postgres 16; `alembic upgrade` → `downgrade` → `upgrade` and `alembic check` clean on Postgres.
- Small fixes along the way: blank `DATABASE_URL=` copied from `.env.example` crashed startup → blank values now fall back to defaults (test added). Generated migration had trailing whitespace → fixed by the pre-commit hook.
- Could not run the Docker stack in the build environment (no Docker daemon); compose file validated with `docker compose config`, container start command simulated from a clean copy. Needs a real `docker compose up` on the laptop.

**Feedback log removed**
- Decision: no daily feedback log. The Devpost submission still has a required feedback field → write 3–4 sentences on submission day.

### 2026-10-06 · Spike run 1 (`scripts/spike.py`)

Models found on Token Factory (4 NVIDIA of 25):
```
nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
nvidia/Nemotron-3-Ultra-550b-a55b
nvidia/Nemotron-3_5-Lightning
nvidia/nemotron-3-super-120b-a12b
```
Omni: NOT FOUND · Safety model: NOT FOUND → Issue #6.

Each tier, no tools, asked "What are your clinic timings?" (system prompt gave **no** hours):
```
nano   1949 ms  "open Monday to Saturday from 9:00 AM to 6:00 PM, and on Sundays from 10:00 AM to 4:00 PM"
super  3003 ms  "9:00 AM to 6:00 PM on weekdays ... 10:00 AM to 2:00 PM on Saturdays. We are closed on Sundays"
ultra   877 ms  "Monday to Saturday from 9:00 AM to 7:00 PM, and on Sundays from 10:00 AM to 2:00 PM"
```
→ Three models, three different invented timetables. → Issue #1 (keep this for the README/video).

Tool test: Super called `find_slots`, then the reply was empty and flagged "⚠ reply didn't quote the tool results".
- Cause: the script allowed only one tool round; Super wanted a second call (`get_fee`). → Issue #5, fixed in the spike.

Hindi reply (Super) invented contact details:
```
... कृपया हमारे फ्रंट डेस्क पर +91-80-XXXXXXXX पर कॉल करें। या आप हमारी वेबसाइट www.sunriseclinic.in पर जाकर ...
```

### 2026-10-06 · Spike run 2 (multi-round tools + Lightning)

Each tier, no tools:
```
nano        1757 ms  in=58 out=133   "open from 9:00 AM – 7:00 PM, Monday through Saturday. We are closed on Sundays."
lightning   1743 ms  in=58 out=400   "Here's a thinking process: 1. **Analyze User Input:** ..."   ← reasoning leaked, hit token cap
super       2768 ms  in=58 out=392   "weekdays 9:00 AM to 6:00 PM, Saturdays 10:00 AM to 2:00 PM. Closed on Sundays."
ultra        969 ms  in=58 out=86    "Monday through Saturday 9:00 AM to 7:00 PM, Sundays 10:00 AM to 2:00 PM"
```
→ Issue #2 (Lightning reasoning leak). Issue #1 again (invented hours, different from run 1).

Tool calling, multi-round:
```
super:      round 1 1244 ms find_slots → round 2 977 ms get_fee → round 3 1063 ms final   (≈3.3 s) ✅
            "Tomorrow, Dr. Rao has openings for new patients at 10:00 AM, 10:40 AM, and 11:20 AM.
             The consultation fee for a new-patient visit is ₹500."
lightning:  round 1 574 ms find_slots + get_fee (parallel) → round 2 492 ms final            (≈1.1 s) ✅
            "Tomorrow morning Dr. Rao has slots at 10:00 am, 10:40 am, and 11:20 am.
             The consultation fee for a new patient is ₹500."
```
→ Lightning ~3× faster and calls tools in parallel. Decision: Lightning = fast tier (intent, rails, tool selection) with reasoning off; Super = patient-facing replies; Ultra = summaries/reviews; Nano = fallback.

Languages (Super):
```
Hindi    27650 ms  "हाँ, आप कल सुबह 9 बजे या 10 बजे के स्लॉट में डॉक्टर राव के साथ अपॉइंटमेंट बुक कर सकते हैं।
                    कृपया अपनी पसंद का समय बताइए, मैं तुरंत बुकिंग कर दूँगा।"
Kannada  29979 ms  "ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಡಾ. ರಾವರಿಗೆ ಅಪಾಯಿಂಟ್ಮೆಂಟ್ ಲಭ್ಯವಿದೆ, ಆದರೆ ಮಿತ Places ಇವೆ. ..."
```
→ Issue #3 (≈10× slower than English). Issue #1: Hindi invented "9 or 10 am" slots and promised to book; Kannada invented "limited places". Issue #4: English word mixed into Kannada.

### 2026-10-06 · Local API run

**`.env.example` showed as deleted**
- Symptom: `git status` → `deleted: .env.example` after creating `.env.local`.
- Cause: the example file was renamed to `.env.local` instead of copied.
- Fix: `git restore .env.example`. `.env.local` stays untracked (git-ignored), as intended. ✅

**API running locally (Windows, Python 3.14)**
- `pip install -r requirements.txt`, `alembic upgrade head`, `uvicorn app.main:app --reload` all worked on Python 3.14 (no 3.12 needed).
- `/docs` shows the ClinicDesk API (v0.1.0) with `GET /health` and `GET /debug/trace`. ✅

---

## How to add an entry

Append under **Log** with today's date. For each problem record: **symptom** (exact output/error), **cause**, **fix**, and paste the key output in a code block. Add or update a row in **Open issues** and change its status when it's resolved.
