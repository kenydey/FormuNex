"""Uvicorn entry point for the E2E stack (nightly workflow + local runs).

Runs the real app, with ONE simulation: the release gate's view of the GHS
rule-set sign-off, patched exactly like backend/tests/conftest.py's
``signed_off_ruleset`` fixture. The shipped rule set stays ``status: draft``
(the real EHS signature is still pending), so without this simulation gate
check G5 can never pass and the PLM publish lifecycle cannot be exercised
end-to-end. Only the gate module's references are patched — classification
itself keeps using the real rule set (same version, same parameters).

    cd frontend/e2e && python -m uvicorn run_backend:app --port 8000
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.plm.ghs.ruleset import RuleSet, RuleSetSignoff, load_ruleset  # noqa: E402
from app.services.plm.release import gate  # noqa: E402

_data = load_ruleset().as_dict()
_data["status"] = "released"
gate.load_ruleset = lambda *a, **k: RuleSet(_data)  # noqa: E731
gate.load_signoff = lambda version: RuleSetSignoff(  # noqa: E731
    signer="EHS 专家（E2E 模拟签核）", signed_at="2026-10-17", version=version,
    decision="approved", comment="E2E 模拟签核，与 backend/tests/conftest.py 的 signed_off_ruleset fixture 同口径。")

from app.main import app  # noqa: E402,F401
