"""Deterministic booking flow: the model extracts what the patient said, the code decides what happens.

Why: in the first live runs (DEVLOG #27, #31) a small model driving the booking tool-by-tool skipped
the confirmation, looped on "3", ignored an invalid phone number and the reply writer invented slots.
Here every step is plain code over the same tools:

    details (name, phone, age) ─┐
    doctor / need, date, time  ─┼→ identify patient → find slots → patient picks → hold → confirmation card
    option number / time       ─┘   (find or register,           (numbered list       (graph interrupt)
                                     consent card once)            shown verbatim)

`run_flow` returns state updates plus `say`: what the reply must contain (questions to ask,
patient-facing problems, options to show, the booking result). The reply writer only words it.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select

from app import models as m
from app.agent.tools import ToolContext, ToolFailure, normalise_phone, run_tool

ASK_TEXT = {
    "name": "their name",
    "phone": "their 10-digit mobile number",
    "age": "their age (they're a new patient)",
    "doctor": "which doctor they'd like, or what the visit is for",
}
_PHONE_RUN = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{7,15}\d)(?!\d)")
_AGE = re.compile(r"\b(?:age[d]?\s*(?:is\s*)?(\d{1,3})|(\d{1,3})\s*(?:years?|yrs?|y/?o)\b)", re.I)
_OPTION = re.compile(r"^\s*(?:option|no\.?|number|#)?\s*(\d)\s*[.)]?\s*$", re.I)
_CLOCK = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", re.I)


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


# --------------------------------------------------------------------------- extraction checks
def merge_details(details: dict, ext: dict, text: str) -> tuple[dict, list[str]]:
    """Add name/phone/age the patient actually typed in `text`. Returns (details, problems)."""
    details, problems = dict(details), []
    typed = _digits(text)

    phone = _digits(str(ext.get("phone") or ""))
    if not phone:
        runs = [_digits(x) for x in _PHONE_RUN.findall(text)]
        phone = max(runs, key=len) if runs else ""
    if phone and len(phone) >= 7 and phone in typed:
        try:
            details["phone"] = normalise_phone(phone)
        except ToolFailure:
            details.pop("phone", None)
            problems.append(f"That phone number has {len(phone)} digits. Please share a 10-digit mobile number.")

    name = str(ext.get("name") or "").strip()
    if name and len(name) >= 2 and name.split()[0].casefold() in text.casefold():
        details["name"] = " ".join(w.capitalize() if w.islower() else w for w in name.split())

    age = ext.get("age")
    if age is None:
        mm = _AGE.search(text)
        age = (mm.group(1) or mm.group(2)) if mm else None
    try:
        age = int(age) if age is not None else None
    except (TypeError, ValueError):
        age = None
    if age is not None and 0 <= age <= 120 and re.search(rf"\b{age}\b", text):
        details["age"] = age
    if ext.get("new_patient") is not None:
        details["new_patient"] = bool(ext["new_patient"])
    return details, problems


def match_doctor(ctx: ToolContext, said: str | None) -> m.Doctor | None:
    if not said:
        return None
    words = {w for w in re.findall(r"[a-z]+", said.casefold()) if len(w) > 2 and w not in ("doctor", "dr")}
    if not words:
        return None
    doctors = ctx.session.scalars(select(m.Doctor).where(m.Doctor.clinic_id == ctx.clinic_id)).all()
    hits = [d for d in doctors if words & set(re.findall(r"[a-z]+", d.name.casefold()))]
    return hits[0] if len(hits) == 1 else None


def clean_date(value: Any, ctx: ToolContext) -> str | None:
    """'today' / 'tomorrow' / ISO date, never in the past (fixes a model that guesses the wrong year)."""
    v = str(value or "").strip().lower()
    if v in ("today", "tomorrow"):
        return v
    try:
        d = date.fromisoformat(v)
    except ValueError:
        return None
    today = ctx.local_now.date()
    if d < today:
        try:
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        except ValueError:
            return None
    return d.isoformat()


def pick_option(offered: list[dict], ext: dict, text: str) -> dict | None:
    """The slot the patient chose: an option number, or a time that matches exactly one option."""
    if not offered:
        return None
    n = ext.get("option")
    mm = _OPTION.match(text)
    if mm:
        n = int(mm.group(1))
    try:
        n = int(n) if n is not None else None
    except (TypeError, ValueError):
        n = None
    if n is not None and 1 <= n <= len(offered):
        return offered[n - 1]
    said = _times(text)
    if said:
        hits = [o for o in offered if _times(o["label"]) & said]
        if len(hits) == 1:
            return hits[0]
    return None


def _times(text: str) -> set[tuple[int, int]]:
    """Clock times mentioned in text as (hour24, minute); bare numbers only count with am/pm or ':'."""
    out = set()
    for h, mnt, ap in _CLOCK.findall(text or ""):
        if not mnt and not ap:
            continue
        h, mi = int(h), int(mnt or 0)
        ap = ap.lower().replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            out.add((h, mi))
            if not ap and h < 12:
                out.add((h + 12, mi))
    return out


def unverified_times(reply: str, allowed_text: str) -> set[tuple[int, int]]:
    """Times in the reply that appear nowhere in the facts or the patient's words."""
    return _times(reply) - _times(allowed_text)


# --------------------------------------------------------------------------- the flow
def _log(results: list, tool: str, args: dict, out: dict) -> dict:
    results.append({"tool": tool, "args": {k: v for k, v in args.items() if k != "phone"}, "result": out})
    return out


def _slot_label(s: dict) -> str:
    if s.get("booking_style") == "token":
        return (f"{s['doctor']} · {s['estimated_time']} · token {s['next_token']} "
                f"({s['patients_ahead']} ahead) · ₹{s['fee_inr']}")
    return f"{s['doctor']} · {s['when']} · ₹{s['fee_inr']}"


def run_flow(state: dict, ctx: ToolContext) -> dict:
    """One step of the booking flow. Returns state updates (incl. `say`, maybe `pending`)."""
    ext = state.get("ext") or {}
    text = next((x["content"] for x in reversed(state.get("messages", [])) if x["role"] == "user"), "")
    booking = dict(state.get("booking") or {})
    results = list(state.get("tool_results", []))
    patient_id = state.get("patient_id")
    say: dict = {"ask": [], "problems": [], "options": None, "booked": None}
    upd: dict = {}

    last = results[-1] if results else None
    # ---- just came back from the confirmation card
    if last and last["tool"] == "book":
        if last["result"].get("ok"):
            appt = last["result"]
            pay = _log(results, "send_payment_link", {"appointment_id": appt["appointment_id"]},
                       run_tool("send_payment_link", {"appointment_id": appt["appointment_id"]}, ctx))
            say["booked"] = {"doctor": appt.get("doctor"), "when": appt.get("when"),
                             "token": appt.get("token_no"), "fee_inr": appt.get("fee_inr"),
                             "pay_url": pay.get("url") if pay.get("ok") else None}
            return {"booking": {}, "tool_results": results, "say": say, "hold_id": None}
        booking["chosen"] = None
        if last["result"].get("error") == "PATIENT_DECLINED":
            say["problems"].append("No problem, nothing was booked. Would you like one of the other times?")
        else:
            say["problems"].append(last["result"].get("message", "That booking didn't go through."))
            booking["offered"] = None
        if booking.get("offered"):
            say["options"] = booking["offered"]
        return {"booking": booking, "tool_results": results, "say": say, "hold_id": None}
    if last and last["tool"] == "register_patient" and not last["result"].get("ok"):
        if last["result"].get("error") == "PATIENT_DECLINED":
            say["problems"].append("Without your consent we can't book online, but our clinic staff can call you to help.")
            return {"booking": {}, "tool_results": results, "say": say}

    # ---- 1. details the patient typed
    details, problems = merge_details(booking.get("details") or {}, ext, text)
    booking["details"] = details
    say["problems"] += problems

    # ---- 2. what they want
    changed = False
    doc = match_doctor(ctx, ext.get("doctor"))
    if doc and doc.id != booking.get("doctor_id"):
        booking.update(doctor_id=doc.id, doctor=doc.name, specialty=None)
        changed = True
    if ext.get("need") and not booking.get("doctor_id") and not booking.get("specialty"):
        r = _log(results, "route_to_doctor", {"need": ext["need"]},
                 run_tool("route_to_doctor", {"need": str(ext["need"])[:200]}, ctx))
        if r.get("ok"):
            booking["specialty"] = r["specialty"]
            booking["need"] = ext["need"]
            changed = True
    for key, value in (("date", clean_date(ext.get("date"), ctx)),
                       ("time", ext.get("time") if re.fullmatch(r"\d{2}:\d{2}", str(ext.get("time") or "")) else None),
                       ("window", ext.get("window") if ext.get("window") in ("morning", "afternoon", "evening") else None)):
        if value and value != booking.get(key):
            booking[key] = value
            changed = True
    if ext.get("follow_up"):
        booking["appointment_type"] = "follow_up"
    if changed:
        booking["offered"], booking["chosen"] = None, None

    # ---- 3. which option they picked
    choice = pick_option(booking.get("offered") or [], ext, text)
    if choice:
        booking["chosen"] = choice

    # ---- 4. who they are
    if not patient_id:
        missing = [f for f in ("name", "phone") if not details.get(f)]
        if not missing:
            fp = _log(results, "find_patient", {"name": details["name"]},
                      run_tool("find_patient", {"name": details["name"], "phone": details["phone"]}, ctx))
            if fp.get("ok"):
                patient_id = fp["patient_id"]
                upd["patient_id"] = patient_id
            elif fp.get("error") == "NOT_FOUND":
                if details.get("age") is None:
                    missing.append("age")
                else:
                    args = {"name": details["name"], "age": details["age"], "phone": details["phone"],
                            "language": state.get("language", "en")}
                    if state.get("consented"):
                        reg = _log(results, "register_patient", args,
                                   run_tool("register_patient", {**args, "consent": True}, ctx))
                        if reg.get("ok"):
                            patient_id = reg["patient_id"]
                            upd["patient_id"] = patient_id
                    else:
                        upd["pending"] = {"call": {"id": "flow_register", "name": "register_patient", "args": args}}
            else:
                say["problems"].append(fp.get("message", "We couldn't check those details."))
        say["ask"] += missing

    # ---- 5. options
    if not booking.get("doctor_id") and not booking.get("specialty"):
        say["ask"].append("doctor")
    elif not booking.get("offered") and not booking.get("chosen"):
        args = {"appointment_type": booking.get("appointment_type", "new"),
                "date": booking.get("date") or "today", "days": 1 if booking.get("date") else 3,
                "window": booking.get("window") or "any", "language": state.get("language", "en")}
        if booking.get("doctor_id"):
            args["doctor_id"] = booking["doctor_id"]
        else:
            args["specialty"] = booking["specialty"]
        if booking.get("time"):
            args["preferred_time"] = booking["time"]
        fs = _log(results, "find_slots", args, run_tool("find_slots", args, ctx))
        if fs.get("ok") and fs.get("slots"):
            booking["offered"] = [{"doctor_id": s["doctor_id"], "start": s["start"], "label": _slot_label(s),
                                   "appointment_type": fs.get("appointment_type", args["appointment_type"])}
                                  for s in fs["slots"][:3]]
            say["options"] = booking["offered"]
        elif fs.get("ok"):
            say["problems"].append("Nothing is free then. Would another day or time work?")
            booking["date"] = None
        else:
            say["problems"].append(fs.get("message", "We couldn't search the schedule."))
            if fs.get("error") == "IN_PAST":
                booking["date"] = None

    # ---- 6. hold the chosen slot → confirmation card
    chosen = booking.get("chosen")
    if chosen and patient_id and "pending" not in upd:
        args = {"doctor_id": chosen["doctor_id"], "start": chosen["start"],
                "appointment_type": chosen["appointment_type"], "patient_id": patient_id}
        h = _log(results, "hold_slot", args, run_tool("hold_slot", args, ctx))
        if h.get("ok"):
            upd["hold_id"] = h["hold_id"]
            upd["pending"] = {"call": {"id": "flow_book", "name": "book",
                                       "args": {"hold_id": h["hold_id"], "patient_id": patient_id}}}
        else:
            say["problems"].append("That time was just taken. Here are fresh options.")
            booking["offered"], booking["chosen"] = None, None
    elif booking.get("offered") and not chosen and not say["options"] and not say["ask"]:
        say["options"] = booking["offered"]                     # remind them of the choices
        say["ask"].append("option")
    elif chosen and not patient_id:
        say["chosen"] = chosen["label"]

    return {**upd, "booking": booking, "tool_results": results, "say": say}


def flow_active(state: dict) -> bool:
    intent = state.get("intent")
    if intent == "book":
        return True
    in_progress = bool(state.get("booking"))
    return in_progress and intent not in ("cancel", "reschedule", "queue", "pay", "human")
