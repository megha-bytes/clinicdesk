"""The clinic's Agent Card, served at /.well-known/agent-card.json."""
from __future__ import annotations

from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill, APIKeySecurityScheme,
                       SecurityRequirement, SecurityScheme, StringList)

from app.settings import get_settings

SKILLS = [
    AgentSkill(id="book_appointment", name="Book an appointment",
               description="Finds free times with a doctor (or the right specialist for a need), registers new patients "
                           "with their consent, and books after the patient confirms. Returns a payment link.",
               tags=["appointments", "booking", "clinic"],
               examples=["Book Dr. Rao tomorrow morning for Asha Reddy, 34, 9123456780",
                         "My daughter needs a skin doctor on Friday evening"]),
    AgentSkill(id="check_availability", name="Check availability",
               description="Lists the next free times with a doctor, on a day or part of the day.",
               tags=["availability", "slots"], examples=["When is Dr. Khan free this week?"]),
    AgentSkill(id="change_appointment", name="Reschedule or cancel",
               description="Moves or cancels an existing appointment after verifying the patient (name + phone) and "
                           "the patient's confirmation.",
               tags=["reschedule", "cancel"], examples=["Cancel my appointment, I'm Ravi Kumar, 9000011111"]),
    AgentSkill(id="queue_status", name="Token queue status",
               description="Current token and estimated wait for a verified patient's appointment today.",
               tags=["queue", "token", "wait time"], examples=["How long until my turn? I'm token 14 with Dr. Nair"]),
    AgentSkill(id="clinic_info", name="Clinic information",
               description="Doctors, specialties, hours, fees and address. Never gives medical advice.",
               tags=["hours", "fees", "doctors"], examples=["What are the clinic timings?"]),
]

POLICY = (
    "Rules for calling agents: ask the patient before answering a consent or confirmation question "
    "(state input-required); never answer them on your own. The clinic never gives medical advice. "
    "Emergencies return 108/112 instructions immediately (metadata escalated=true): relay them to the patient."
)


def agent_card() -> AgentCard:
    base = get_settings().public_base_url.rstrip("/")
    return AgentCard(
        name="Sunrise Family Clinic front desk (ClinicDesk)",
        description="Books, reschedules and cancels appointments at Sunrise Family Clinic, Bengaluru, in English, Hindi "
                    "or Kannada. " + POLICY,
        version="1.0.0",
        provider=AgentProvider(organization="ClinicDesk", url=base),
        supported_interfaces=[AgentInterface(url=f"{base}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        skills=SKILLS,
        security_schemes={"apiKey": SecurityScheme(api_key_security_scheme=APIKeySecurityScheme(
            location="header", name="X-API-Key", description="Per-caller key issued by the clinic."))},
        security_requirements=[SecurityRequirement(schemes={"apiKey": StringList(list=[])})],
    )
