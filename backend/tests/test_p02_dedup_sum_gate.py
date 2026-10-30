"""P0-2: lever dedup (alias + role) and the DOE wt% sum gate.

Covers the observed API case: user levers "树脂"/"固化剂" must replace the
baseline "Bisphenol-A epoxy (DGEBA)"/"Polyamide hardener" levers instead of
coexisting with them, and runs whose continuous wt% factors sum above the
BayBE cap are flagged infeasible without touching already-flagged runs.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.domain.project_spec import _merge_explicit_levers
from app.domain.schemas import DOEFactor, DOEPlan, DOERun, LeverSpec
from app.main import app
from app.services.engines.doe_registry import apply_wt_sum_gate

DGEBA = "Bisphenol-A epoxy (DGEBA)"
HARDENER = "Polyamide hardener"
PHOSPHATE = "Zinc phosphate"


def _lever(name, low, high):
    return LeverSpec(name=name, low=low, high=high, unit="wt%")


def _baseline():
    return [
        _lever(DGEBA, 26.6, 49.4),
        _lever(HARDENER, 9.8, 18.2),
        _lever(PHOSPHATE, 2.8, 5.2),
    ]


def _names(levers):
    return [l.name for l in levers]


# ── dedup ────────────────────────────────────────────────────────────────────

def test_alias_replaces_baseline():
    merged = _merge_explicit_levers(
        _baseline(), [_lever("双酚a环氧树脂", 10.0, 40.0)]
    )
    assert _names(merged) == ["双酚a环氧树脂", HARDENER, PHOSPHATE]
    assert merged[0].low == 10.0 and merged[0].high == 40.0


def test_role_replaces_single_candidate():
    merged = _merge_explicit_levers(
        _baseline(),
        [_lever("树脂", 10.0, 40.0), _lever("固化剂", 5.0, 20.0)],
    )
    assert _names(merged) == ["树脂", "固化剂", PHOSPHATE]
    assert (merged[0].low, merged[0].high) == (10.0, 40.0)
    assert (merged[1].low, merged[1].high) == (5.0, 20.0)
    assert (merged[2].low, merged[2].high) == (2.8, 5.2)


def test_role_ambiguous_keeps_both(caplog):
    derived = [
        _lever(DGEBA, 26.6, 49.4),
        _lever("Waterborne acrylic emulsion", 20.0, 40.0),
        _lever(HARDENER, 9.8, 18.2),
    ]
    merged = _merge_explicit_levers(derived, [_lever("树脂", 10.0, 40.0)])
    assert _names(merged) == [
        DGEBA,
        "Waterborne acrylic emulsion",
        HARDENER,
        "树脂",
    ]
    assert "ambiguous" in caplog.text


def test_alias_resolves_both_sides():
    merged = _merge_explicit_levers(
        [_lever("磷酸锌", 2.8, 5.2)],
        [_lever("Zinc phosphate", 1.0, 6.0)],
    )
    assert _names(merged) == ["Zinc phosphate"]


def test_supersede_last_wins_no_duplication():
    merged = _merge_explicit_levers(
        _baseline(),
        [_lever("树脂", 10.0, 40.0), _lever("双酚a环氧树脂", 15.0, 35.0)],
    )
    assert _names(merged) == ["双酚a环氧树脂", HARDENER, PHOSPHATE]
    assert (merged[0].low, merged[0].high) == (15.0, 35.0)


def test_exact_name_still_replaces():
    merged = _merge_explicit_levers(
        _baseline(), [_lever(DGEBA, 10.0, 40.0)]
    )
    assert _names(merged) == [DGEBA, HARDENER, PHOSPHATE]
    assert (merged[0].low, merged[0].high) == (10.0, 40.0)


# ── sum gate ─────────────────────────────────────────────────────────────────

def _plan(runs):
    factors = [
        DOEFactor(name="树脂", low=10.0, high=40.0, unit="wt%"),
        DOEFactor(name="固化剂", low=5.0, high=20.0, unit="wt%"),
        DOEFactor(name=PHOSPHATE, low=2.8, high=5.2, unit="wt%"),
    ]
    return DOEPlan(design="ccd", factors=factors, runs=runs)


def _run(run_id, natural, **kw):
    return DOERun(run_id=run_id, coded={}, natural=natural, **kw)


def test_gate_ignores_at_cap_corner_and_flags_over_cap():
    plan = apply_wt_sum_gate(
        _plan(
            [
                _run(1, {"树脂": 40.0, "固化剂": 20.0, PHOSPHATE: 5.2}),
                _run(2, {"树脂": 40.0, "固化剂": 20.0, PHOSPHATE: 5.2 + 1.0}),
            ]
        )
    )
    assert not plan.runs[0].infeasible  # 65.2 == cap, not over
    assert plan.runs[1].infeasible
    assert "合计" in (plan.runs[1].infeasible_reason or "")


def test_gate_preserves_existing_reason():
    plan = apply_wt_sum_gate(
        _plan(
            [
                _run(
                    1,
                    {"树脂": 40.0, "固化剂": 20.0, PHOSPHATE: 9.9},
                    infeasible=True,
                    infeasible_reason="星点超出因子范围",
                )
            ]
        )
    )
    assert plan.runs[0].infeasible_reason == "星点超出因子范围"


def test_gate_noop_for_single_wt_factor_and_factorless_plan():
    single = DOEPlan(
        design="ccd",
        factors=[DOEFactor(name="树脂", low=10.0, high=40.0, unit="wt%")],
        runs=[_run(1, {"树脂": 99.0})],
    )
    assert not apply_wt_sum_gate(single).runs[0].infeasible
    fake = SimpleNamespace(runs=[])
    assert apply_wt_sum_gate(fake) is fake


# ── API end to end ───────────────────────────────────────────────────────────

client = TestClient(app)


def _post(body, query="design=ccd&engine=native"):
    return client.post(f"/api/doe?{query}", json=body)


def test_api_default_ccd_factors_are_deduped():
    body = {
        "product_type": "x",
        "application": "y",
        "domain": "anticorrosion_coating",
        "levers": [
            {"name": "树脂", "low": 10, "high": 40, "unit": "wt%"},
            {"name": "固化剂", "low": 5, "high": 20, "unit": "wt%"},
        ],
    }
    r = _post(body)
    assert r.status_code == 200, r.text
    plan = r.json()
    # wt% factors are deduped; cure_temperature_c is a process lever and
    # unaffected by the material dedup.
    wt_names = [f["name"] for f in plan["factors"] if f["unit"] == "wt%"]
    assert wt_names == ["树脂", "固化剂", PHOSPHATE]
    assert not any(run["infeasible"] for run in plan["runs"])


def test_api_gate_flags_over_cap_corners_but_not_center():
    body = {
        "product_type": "x",
        "application": "y",
        "domain": "anticorrosion_coating",
        "levers": [
            {"name": "树脂", "low": 30, "high": 60, "unit": "wt%"},
            {"name": "固化剂", "low": 30, "high": 60, "unit": "wt%"},
        ],
    }
    r = _post(body)
    assert r.status_code == 200, r.text
    plan = r.json()
    flagged = [run for run in plan["runs"] if run["infeasible"]]
    assert flagged
    assert all("合计" in (run["infeasible_reason"] or "") for run in flagged)
    center = [
        run
        for run in plan["runs"]
        if all(abs(v) < 1e-9 for v in run["coded"].values())
    ]
    assert center and not any(run["infeasible"] for run in center)
