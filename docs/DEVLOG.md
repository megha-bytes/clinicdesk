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
| 10 | First deploy (Render + Neon) not done yet | Oct 6 | ✅ Resolved | Live at https://clinicdesk-api.onrender.com (Render free, Singapore; Neon free, Singapore). `/health` → prod, db ok, key set |
| 11 | Render health check polled `/health` every few seconds; each check queries Neon, so the DB never scales to zero. Neon free = 100 CU-hours/month; 24/7 at 0.25 CU ≈ 180 → DB would be suspended mid-month (possibly during judging) | Oct 6 | ✅ Resolved | Added `/ping` (no DB). Render health check path set to `/ping`; UptimeRobot monitors `/ping` every 5 min. (Still worth glancing at Neon Monitoring to confirm compute goes idle) |
| 12 | Screenshot of Render env vars showed most of the Token Factory prod key, and part of the Neon connection string | Oct 6 | ✅ Resolved | Rotated: new Token Factory prod key (old one deleted) + Neon password reset; both updated on Render. Rule: never screenshot env-var values |
| 13 | Slot ranking ignored an explicit time-of-day request ("evening") when the preferred time was outside it, and offered 12:40 instead | Oct 6 | ✅ Resolved | Closeness is now measured from the nearest edge of the requested window; test `test_requested_window_beats_closeness` |
| 14 | `ZoneInfo("Asia/Kolkata")` fails on Windows (no system timezone database) | Oct 6 | ✅ Resolved | Added `tzdata` to requirements; reproduced the error and the fix |
| 15 | Routing sent "child is coughing" (Kannada) and "my baby has a rash" to the GP / dermatologist instead of the pediatrician | Oct 7 | ✅ Resolved | Routing rules now have a clinic-set `priority` (pediatrics = 10); highest priority wins, then the most specific keyword |
| 16 | SQLite drops timezone offsets, so a 10:00 IST booking would read back as 10:00 UTC (3:30 pm IST) | Oct 7 | ✅ Resolved | All timestamps stored in UTC; converted to clinic time on the way out; day queries use UTC bounds |
| 17 | Demo data can't be loaded on Render free (no shell to run `python -m app.seed`) | Oct 7 | 🟡 Needs setup | `POST /admin/reset-demo` (needs `DEMO_RESET_TOKEN`, hidden from docs, 404 when unset) + nightly GitHub Action. Set the token on Render and in GitHub secrets, then call it once |
| 18 | Day 5 patch failed on the laptop (`patch failed: docs/DEVLOG.md`) because the engine patch hadn't been applied first | Oct 7 | ✅ Resolved | `git am --abort`, applied engine then Day 5. From now on patches are numbered and say which commit they need |
| 19 | Tests read the developer's real `.env.local`: `/health` test saw `token_factory_key == "set"` on the laptop (passed in CI and the build environment, which have no `.env.local`) | Oct 7 | ✅ Resolved | Test mode never loads `.env.local`; regression test added. Verified the app still reads it in normal runs |

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

### 2026-10-06 · First deploy (Render + Neon)

**Neon**
- Project `clinicdesk`, AWS Asia Pacific 1 (Singapore), Postgres only (object storage, functions, AI gateway and auth left off).
- Used the **direct** connection string (connection pooling toggled off) for migrations.

**Render**
- Web Service from `megha-bytes/clinicdesk`, Docker, branch `main`, root `backend/`, Singapore, Free (0.1 CPU, 512 MB).
- Env vars: `APP_ENV=prod`, `DATABASE_URL` (Neon), `TOKEN_FACTORY_API_KEY` (prod key).
- Build log:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 28be1ddbc533, initial schema
INFO:     Uvicorn running on http://0.0.0.0:10000
INFO:     ... "GET /health HTTP/1.1" 200 OK
==> Your service is live 🎉
==> Available at your primary URL https://clinicdesk-api.onrender.com
```
- External check:
```
{"status":"ok","version":"0.1.0","env":"prod","db":"ok","token_factory_key":"set"}
```
→ Issue #10 resolved. ✅

**Problems spotted in the log**
- `GET /health` every few seconds from Render's health checker, and each one runs `SELECT 1` on Neon → Issue #11. Fix: `/ping` endpoint (no DB); Render health check and UptimeRobot moved to `/ping`.
- `GET /` and `/favicon.ico` → 404 for visitors. Fix: `/` now returns the API name and links to `/docs` and `/health`.
- Tests: 20 passed on SQLite and Postgres (new: `/ping` never touches the DB, `/` points to docs).

**Secret exposure in a screenshot**
- A screenshot of Render's Environment Variables page showed most of the Token Factory prod key and part of the Neon URL → Issue #12. Rule from now on: hide values (eye icon) or crop them out before sharing screenshots.

**Build environment hiccup**
- A `sed` edit with `#` in the replacement text failed silently in a command chain, so a commit briefly went out without the config and log changes; fixed by amending before the patch was shared. Lesson: use small Python edits, not `sed`, for files with special characters.

### 2026-10-06 · Scheduling engine (Days 3–4)

Built `backend/app/engine/`: pure Python, no DB or model calls, so every booking decision is deterministic.
- `availability.py`: sessions minus breaks, leave, daily caps; `validate_slot` raises a coded `BookingError` (`IN_PAST`, `ON_LEAVE`, `OUTSIDE_HOURS`, `ON_BREAK`, `OVERLAP`, `DAILY_CAP_REACHED`); `free_slots` on a 10-min grid with lead time.
- `operations.py`: hold (expires after 5 min) → confirm (only a live hold; re-validates; assigns token numbers in token style), cancel, reschedule (keeps length, can change doctor), check-in → start → complete, no-show; `check_duration` stops the model inventing appointment lengths (`WRONG_DURATION`).
- `scoring.py`: ranks valid slots (requested window/doctor, closeness, short gaps left, same doctor as last visit, language) and returns the top 3 at least 30 min apart; deterministic tie-breaks.
- `queue.py`: token numbers never reused; order is by token, only staff-urgent jumps ahead (no payment field exists, so payment can't affect order); wait = time until session opens + remainder of current consult + patients ahead × pace; pace blends the doctor's average with today's completed consults (full trust after 5).
- `waitlist.py`: offers a freed slot to the earliest matching request that fits.

Tests: **82 passed** on SQLite and Postgres (62 engine tests + 20 earlier).

Problems found while building:
- The first test run failed one ranking test: asking for "evening" with a preferred time of 13:00 offered 12:40 → Issue #13, fixed.
```
FAILED tests/engine/test_scoring_waitlist.py::test_requested_window_beats_closeness
1 failed, 61 passed
```
- Timezones on Windows → Issue #14, fixed with `tzdata`:
```
zoneinfo._common.ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'
```
- Build-environment only: the temporary local Postgres used for testing stopped between steps because sandbox folder permissions reset; restarted it. Not relevant to the laptop or Render.

### 2026-10-07 · Day 5: seed data, tools, payments, events

Built:
- **Migration 2**: appointment `started_at`/`completed_at` (live queue), doctor `booking_style` (one doctor timed, another token queue), routing `priority`. Tested up/down on Postgres, including upgrading a DB that already has routing rules.
- **`app/seed.py`**: Sunrise Family Clinic (demo), 4 doctors: Dr. Ananya Rao (GP, timed), Dr. Meera Nair (GP, **token queue**), Dr. Vikram Iyer (dermatology, Mon/Wed/Fri, cap 18), Dr. Farah Khan (pediatrics, on leave in 3 days); 3 appointment types (new 20 min ₹500, follow-up 10 min ₹300, dressing 15 min ₹400); routing keywords in English, Hindi and Kannada; 60 fake patients (phones 9000000001–60); ~234 appointments over a week; today's past visits marked completed with realistic times, one "with the doctor"; 2 waitlist entries. Idempotent; `--reset` rebuilds.
- **`app/agent/tools.py`**: 13 model tools + 1 guardrail-only tool (`raise_emergency_alert`, never offered to the model). Typed inputs, engine-validated decisions, coded errors (never exceptions), privacy-safe lookups (same NOT_FOUND for wrong name and unknown phone), audit log on every change, OpenAI function-calling specs generated from the input models.
- **Payments**: `MockPaymentProvider` (link to our `/pay/<id>` page with a "TEST PAYMENT" label and a pay button); `RazorpayProvider` (test keys only, refuses `rzp_live_`), webhook with HMAC signature check. Amount always from the fee table; link creation is idempotent.
- **`app/realtime/events.py`**: `ExecutionEvent` schema + factory with increasing `seq`.
- **`/admin/reset-demo`** + nightly GitHub Action (03:00 IST) → Issue #17.

Tests: **131 passed** on SQLite and Postgres (49 new).

Live end-to-end run (local server + Postgres):
```
--- reset (wrong token): 401
--- reset: {"created":true,"doctors":4,"patients":60,"appointments":234,"first_day":"2026-10-07"}
route: General Physician                      ← "मुझे बुखार है"
slots: ['Thu 8 Oct, 9:10 AM', 'Thu 8 Oct, 9:40 AM', 'Thu 8 Oct, 10:30 AM']
booked: Dr. Ananya Rao Thu 8 Oct, 9:10 AM confirmed
link: http://localhost:8079/pay/<id> 500
--- pay page: TEST PAYMENT · no real money · ₹500 · Pay ₹500 (test)
--- pay: ✓ Paid        → payment row: paid | 500 | paid_at set
```

Problems found while building:
- Routing priority → Issue #15 (caught by tests).
```
FAILED test_routing_uses_clinic_table[ಮಗು ಕೆಮ್ಮುತ್ತಿದೆ-Pediatrician]
FAILED test_routing_uses_clinic_table[my baby has a rash-Pediatrician]
```
- SQLite timezone handling → Issue #16 (spotted while writing the adapter, before it caused a bug).
- Test helper `call(ctx, name, **args)` clashed with tools that take a `name` argument (`TypeError: got multiple values for argument 'name'`); renamed to `tool`. Test-only.
- `/ping` registered for GET+HEAD under one name produced a duplicate-operation warning in the API docs; split into separate GET and hidden HEAD routes.
- The `priority` column is NOT NULL; added a server default so the migration works on databases that already have rows.

### 2026-10-07 · Applying Day 5 on the laptop

**Patch order**
```
git am clinicdesk-day5.patch
error: patch failed: docs/DEVLOG.md:21
error: docs/DEVLOG.md: patch does not apply
```
- Cause: the engine patch (Days 3–4) hadn't been applied, so the dev log didn't contain the lines Day 5 expected.
- Fix: `git am --abort`, then engine patch, then Day 5 → applied. → Issue #18.

**Test failure only on the laptop**
```
FAILED tests/test_health.py::test_health_reports_ok_and_db - AssertionError: assert 'set' == 'missing'
1 failed, 130 passed
```
- Cause: `conftest.py` removed the key from the environment, but `get_settings()` then loaded `.env.local` (the real dev key) back in. CI and the build environment have no `.env.local`, so they passed.
- Fix: settings skip `.env.local` when `APP_ENV=test`; regression test `test_tests_never_read_dot_env_local`. Reproduced with a fake `.env.local` (1 failed) → fixed (132 passed, with and without the file) → confirmed normal runs still read the key. → Issue #19.

---

## How to add an entry

Append under **Log** with today's date. For each problem record: **symptom** (exact output/error), **cause**, **fix**, and paste the key output in a code block. Add or update a row in **Open issues** and change its status when it's resolved.
