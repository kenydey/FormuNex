"""Batch 3: regressions for P1-C11 (minimum category), P1-C12 (RoHS limits),
P1-C13/C14 (snapshot dual shape), P2-C26 (lineage root)."""
from __future__ import annotations

import uuid

import pytest

from app.api.formulations import _redact_snapshot
from app.domain.schemas import Formulation
from app.domain.schemas.doe import Ingredient
from app.services.compliance_rules import check_compliance
from app.services.formulation_history import describe_diff, diff_snapshots
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties


def test_redact_snapshot_handles_pct_shape():
    red = _redact_snapshot({"ingredients": [{"name": "A", "pct": 42.5, "cas": "1-2-3"}]})
    ing = red["ingredients"][0]
    assert "pct" not in ing and "weight_pct" not in ing
    assert ing["weight_pct_band"] and "42.5" not in str(ing)


def test_diff_snapshots_handles_pct_shape_and_warns_on_unknown_key():
    before = {"ingredients": [{"name": "A", "pct": 10}, {"name": "B", "pct": 90}]}
    after = {"ingredients": [{"name": "A", "pct": 50}, {"name": "B", "pct": 50}]}
    d = diff_snapshots(before, after)
    adjusted = {c.name: (c.before_pct, c.after_pct) for c in d.ingredient_changes if c.change == "adjusted"}
    assert adjusted == {"A": (10.0, 50.0), "B": (90.0, 50.0)}
    d2 = diff_snapshots({"ingredients": [{"name": "A", "weight_pct": 10}]},
                        {"ingredients": [{"name": "A", "amount": 20}]})
    assert d2.warnings and "无法识别" in describe_diff(d2)


def test_minimum_category_records_data_gap():
    h = ComponentHazard("skin_corrosion", "3", "H316", is_minimum=True)
    result = classify([Component("A", 100, hazards=[h])], MixtureProperties())
    assert any(g.code == "minimum_category" for g in result.data_gaps)


def _form(name, cas, pct):
    return Formulation(name="F", domain="anticorrosion_coating",
                       ingredients=[Ingredient(name=name, role="pigment", cas_no=cas, weight_pct=pct)])


def test_rohs_below_limit_warns_not_infeasible():
    r = check_compliance(_form("Lead oxide", "1317-36-8", 0.05))
    assert r.compliant and r.status == "warn" and not r.rohs_hits
    r = check_compliance(_form("Lead oxide", "1317-36-8", 0.2))
    assert not r.compliant and r.status == "infeasible" and r.rohs_hits
    r = check_compliance(_form("Cadmium", "7440-43-9", 0.005))
    assert r.compliant and r.status == "warn"
    r = check_compliance(_form("Cadmium", "7440-43-9", 0.02))
    assert not r.compliant and r.status == "infeasible"


@pytest.fixture()
def hist(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'hist.db'}")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.services.formulation_history import FormulationHistoryStore

    factory = default_session_factory()
    with factory() as s:
        root = FormulationVersion(id=str(uuid.uuid4()), lineage_id="lin-1", version=3,
                                  parent_version_id=None, name="导入配方", domain="coating",
                                  snapshot={})
        s.add(root)
        s.flush()
        child = FormulationVersion(id=str(uuid.uuid4()), lineage_id="lin-1", version=4,
                                   parent_version_id=root.id, name="导入配方", domain="coating",
                                   snapshot={})
        s.add(child)
        s.commit()
    yield FormulationHistoryStore(factory)
    get_settings.cache_clear()


def test_find_lineages_matches_imported_non_v1_root(hist):
    assert hist.find_lineages(name="导入配方") == ["lin-1"]
