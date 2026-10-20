# SafeGuard Edge — Live Demo (Python backend + Gemini)

A real backend, not a mock. Two endpoints call the Gemini API directly:

- `POST /api/fuse-risk` — sends the four Bay 7 sensor/permit/RFID readings to Gemini,
  which reasons over them and returns a compound risk score, its reasoning, and a
  recommendation.
- `POST /api/generate-report` — once risk crosses threshold, sends the scenario to
  Gemini and gets back a drafted DGFASLI-style incident report.

The frontend (`static/index.html`) is the same console UI from before, now calling
your backend instead of hitting an LLM API from the browser.

## 1. Get a Gemini API key

Create one at https://aistudio.google.com/apikey if you don't have one yet.

## 2. Set it in your environment

```bash
export GEMINI_API_KEY="your-key-here"
# optional — defaults to gemini-3.5-flash
export GEMINI_MODEL="gemini-3.5-flash"
```

(Or copy `.env.example` to `.env` and fill it in — `python-dotenv` is installed but
you'll need to load it yourself if you want `.env` auto-loaded; simplest for a live
demo is just `export` in your shell.)

## 3. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Run it

```bash
uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly, so
there's nothing else to start.

## 5. Demo flow

1. Click **Load Bay 7 Scenario** — populates the four systems with the case-study
   readings.
2. Click **Run Risk Fusion Agent (AI)** — this hits `/api/fuse-risk`, which calls
   Gemini live. Watch the agent log and the risk gauge update with a real,
   freshly-computed score and explanation (re-run it and the wording will differ
   slightly each time — proof it's not scripted).
3. Once risk crosses 0.75, click **Generate Incident Report (AI)** — this hits
   `/api/generate-report`, which calls Gemini again to draft a full incident report
   from the scenario data.

## Files

```
backend/
├── app.py              FastAPI app, Gemini calls, serves the frontend
├── requirements.txt
├── .env.example
├── static/
│   └── index.html       the console UI
└── README.md
```

## Notes for judges / production hardening

- CORS is wide open (`allow_origins=["*"]`) for local-demo convenience — restrict
  this before deploying anywhere real.
- The Gemini API key never touches the browser — it lives only in the backend
  process's environment, which is the correct pattern for production (the earlier
  browser-only version of this demo was explicitly a shortcut for a time-boxed
  hackathon demo, not something to ship).
- `/api/health` returns whether a key is configured, useful for a quick sanity
  check before you go on stage:
  ```bash
  curl http://localhost:8000/api/health
  ```
