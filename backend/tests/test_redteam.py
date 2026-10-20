"""Red-team suite (scripted mode): every case in redteam/cases.yaml must pass. See redteam/runner.py."""
import pytest

from redteam.runner import load_cases, run_scripted
from app.seed import seed
from tests.test_booking_flow import NOW

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_redteam_case(case, session):
    clinic_id = seed(session, now=NOW)["clinic_id"]
    result = run_scripted(case, session, clinic_id, NOW)
    assert result.passed, "\n".join(result.failures)


def test_suite_size_and_coverage():
    cats = {c["category"] for c in CASES}
    assert len(CASES) >= 50
    assert {"emergency", "injection", "privacy", "medical", "identity", "consent", "facts", "payment"} <= cats
