"""Run the ClinicDesk red-team suite and write a report.

    cd backend
    py ..\\scripts\\redteam.py                         # scripted mode (fake model, local in-memory DB)
    py ..\\scripts\\redteam.py --live https://clinicdesk-api.onrender.com   # real models on the deployed app

Writes docs/redteam-<mode>.md and docs/redteam-<mode>.json (pass rate per category, failures).
Live mode reads REDTEAM_TOKEN from the environment to skip the per-IP rate limit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("APP_ENV", "test")

from redteam.runner import load_cases, report_markdown, run_live, run_scripted, summarise  # noqa: E402


def scripted(cases):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.seed import seed
    from tests.test_booking_flow import NOW
    results = []
    for case in cases:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            if case["turns"][0].get("say") == "@@LONG@@":     # an HTTP-level check; covered by the pytest suite
                from redteam.runner import Result
                results.append(Result(case["id"], case["category"], True, skipped=True))
                continue
            results.append(run_scripted(case, s, seed(s, now=NOW)["clinic_id"], NOW))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="URL", help="run against a deployed backend with the real models")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args()
    cases = load_cases()
    if args.live:
        token = os.environ.get("REDTEAM_TOKEN")
        results = []
        for c in cases:
            r = run_live(c, args.live, token=token)
            results.append(r)
            print(f"{'skip' if r.skipped else 'PASS' if r.passed else 'FAIL'}  {c['id']}" +
                  ("" if r.passed else "  " + "; ".join(r.failures)), flush=True)
        mode = "live"
    else:
        results = scripted(cases)
        mode = "scripted"
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"redteam-{mode}.md").write_text(report_markdown(results, mode, when), encoding="utf-8")
    summary = summarise(results)
    (out / f"redteam-{mode}.json").write_text(json.dumps(
        {"mode": mode, "run_at": when, **summary,
         "failures": [{"id": r.id, "category": r.category, "failures": r.failures}
                      for r in results if not r.passed and not r.skipped]}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{mode}: {summary['passed']}/{summary['total']} passed")
    for cat, v in summary["by_category"].items():
        print(f"  {cat:10} {v['passed']}/{v['total']}")
    sys.exit(0 if summary["passed"] == summary["total"] else 1)


if __name__ == "__main__":
    main()
