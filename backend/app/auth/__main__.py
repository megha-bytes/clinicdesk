"""Create or disable staff accounts from the command line (passwords are typed, never passed as arguments).

    py -m app.auth create --email owner@clinic.in --name "Dr. Mehta" --role owner
    py -m app.auth create --email ravi@clinic.in --name "Ravi" --role receptionist
    py -m app.auth create --email rao@clinic.in --name "Dr. Ananya Rao" --role doctor --doctor "Ananya Rao"
    py -m app.auth disable --email ravi@clinic.in
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app import auth
from app import models as m
from app.db import SessionLocal


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="py -m app.auth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--email", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--role", required=True, choices=auth.ROLES)
    c.add_argument("--doctor", help="part of the doctor's name (doctor accounts)")
    d = sub.add_parser("disable")
    d.add_argument("--email", required=True)
    a = ap.parse_args(argv)
    with SessionLocal() as s:
        from app.chat_api import _demo_clinic_id
        clinic_id = _demo_clinic_id(s)
        if a.cmd == "disable":
            u = s.scalar(select(m.StaffUser).where(m.StaffUser.email == a.email.lower()))
            if not u:
                print("No such account."); return 1
            u.active = False
            for sess in s.scalars(select(m.StaffSession).where(m.StaffSession.user_id == u.id)):
                sess.revoked = True
            s.commit(); print(f"Disabled {u.email} and ended their sessions."); return 0
        doctor_id = None
        if a.role == "doctor":
            doc = s.scalar(select(m.Doctor).where(m.Doctor.clinic_id == clinic_id, m.Doctor.name.ilike(f"%{a.doctor or ''}%")))
            if not a.doctor or not doc:
                print("Give --doctor with part of the doctor's name."); return 1
            doctor_id = doc.id
        pw = getpass.getpass("Password (10+ characters): ")
        if pw != getpass.getpass("Again: "):
            print("Passwords don't match."); return 1
        try:
            u = auth.create_user(s, clinic_id=clinic_id, email=a.email, name=a.name, role=a.role, password=pw, doctor_id=doctor_id)
        except ValueError as e:
            print(e); return 1
        s.commit(); print(f"Created {u.role} account {u.email}."); return 0


if __name__ == "__main__":
    sys.exit(main())
