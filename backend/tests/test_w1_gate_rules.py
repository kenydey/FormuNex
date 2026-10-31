"""W1-13: gate rules — G2 per regulation, G7 UN grouping, Pareto NaN, KG note."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_pareto_nan_rows_never_occupy_frontier():
    from app.domain.schemas import ObjectiveSpec
    from app.services.tradeoff_analysis import compute_pareto_mask, compute_pareto_ranks
    objs = [ObjectiveSpec(metric="a", direction="maximize"), ObjectiveSpec(metric="b", direction="maximize")]
    # Old behavior: the NaN row compared as neither dominated nor dominating
    # and sat on front 0 forever.
    assert compute_pareto_ranks([[float("nan"), 10.0], [5.0, 5.0]], objs) == [None, 0]
    assert compute_pareto_mask([[float("nan"), 10.0], [5.0, 5.0]], objs) == [False, True]
    assert compute_pareto_ranks([[float("nan"), 1.0], [float("nan"), 2.0]], objs) == [None, None]
    assert compute_pareto_ranks([[1.0, 2.0], [2.0, 1.0], [1.0, 1.0]], objs) == [0, 0, 1]


def _doc(doc_id, lang, reg, un, density=None):
    facts = {"un_number": un}
    if density is not None:
        facts["properties"] = {"density_g_cm3": density}
    return SimpleNamespace(id=doc_id, doc_type="sds", lang=lang, regulation=reg, version_no=1,
                           content={"meta": {"facts": facts}})


def test_g7_un_number_grouped_by_regulation():
    from app.services.plm.release.gate import Check, _check_consistency
    c = Check("consistency", "G7", "t")
    _check_consistency(c, [_doc("d1", "zh-CN", "CN", "1993"), _doc("d2", "zh-CN", "EU", "1263")])
    assert not [i for i in c.items if i["key"].startswith("un_number")]
    c = Check("consistency", "G7", "t")
    _check_consistency(c, [_doc("d1", "zh-CN", "EU", "1263"), _doc("d2", "en-US", "EU", "1993")])
    assert len([i for i in c.items if i["key"].startswith("un_number")]) == 1
    assert "[EU]" in c.items[0]["message"]
    c = Check("consistency", "G7", "t")
    _check_consistency(c, [_doc("d1", "zh-CN", "CN", "1993", density=1.35),
                           _doc("d2", "en-US", "CN", "1993", density=1.45)])
    assert any("density_g_cm3" in i["message"] for i in c.items)


def test_g2_evaluates_per_regulation(monkeypatch):
    """Wiring: G2 calls ruleset_for per document regulation; a failure under
    EU must not masquerade as a CN failure (or vice versa)."""
    from app.services.plm.release import gate

    def fake_build(session, items, rs, **kw):
        # rs is the ruleset name returned by the mocked load_ruleset: the EU
        # assessment sees only a weak source, the CN one a trusted source.
        hazards = [SimpleNamespace(source="pubchem")] if rs == "rs-EU" else [SimpleNamespace(source="cn_catalog")]
        comp = SimpleNamespace(name="二甲苯", cas="1330-20-7", pct=30.0, assessed=True,
                               hazards=hazards, substance_id=None)
        return SimpleNamespace(components=[comp])

    monkeypatch.setattr(gate, "ruleset_for", lambda reg: f"rs-{reg}")
    monkeypatch.setattr(gate, "load_ruleset", lambda name: name)
    monkeypatch.setattr(gate, "build_components", fake_build)
    c = gate.Check("substance_data", "G2", "t")
    comps = gate._check_substances(None, c, [{"name": "二甲苯", "pct": 30.0}], ["CN", "EU"])
    assert len(comps) == 1
    assert c.evidence["regulations"] == ["CN", "EU"]
    fails = [(i["key"], i["message"]) for i in c.items]
    assert len(fails) == 1
    assert fails[0][0].endswith(":EU") and "[EU]" in fails[0][1] and "置信度不足" in fails[0][1]


@pytest.fixture()
def factory(tmp_path):
    from app.db.database import Base, make_engine, make_session_factory
    engine = make_engine(f"sqlite:///{tmp_path}/w113.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_kg_gate_note_reaches_dispatch_response(factory):
    """W1-13 (D14): fail-open stays, but the skip note reaches the scheduler."""
    from app.db import doe_plan_store
    from app.domain.schemas import DOEFactor, DOEPlan, DOERun, ProductDomain
    from app.services.lab import tasks
    plan = DOEPlan(design="lhs", factors=[DOEFactor(name="x", low=0.0, high=1.0)],
                   runs=[DOERun(run_id=1, coded={"x": 0.0}, natural={"x": 0.0})],
                   plan_id="dp-kg", domain=ProductDomain.surface_treatment,
                   notes="KG chemical gate skipped: boom")
    with factory() as s:
        doe_plan_store.save(s, plan, project_id="proj-A")
        s.commit()
        out = tasks.dispatch_plan(s, "dp-kg", metrics=[{"metric": "m"}], user_id="u1", project_id="proj-A")
    assert out["plan_notes"] is not None and "KG chemical gate skipped" in out["plan_notes"]
    assert out["created"]
