"""Stage 4 / SR-022: pure PLM gate inputs (GateInput) and evaluate_input.

- evaluate_input runs on a hand-built GateInput with no database.
- The three checks that need the live GHS engine / regulatory data are
  reported not_applicable, never invented.
- On a real revision, the pure checks from evaluate_input agree with
  the pure checks from evaluate() (adapter fidelity).
"""

import ast
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import ExperimentRow, FormulationVersion, MeasurementRow
from app.db.models_lab import MeasurementPickRow
from app.db.models_plm import ProductRevisionRow, RegulatoryDocumentRow, ReleaseGateWaiverRow
from app.domain.gate_input import (
    DocumentData,
    FormulationData,
    GateInput,
    MeasuredValue,
    WaiverData,
)
from app.services.plm.release import gate


def _check(result, key):
    return next(c for c in result["checks"] if c["key"] == key)


def _base_input(**over):
    kw = dict(
        revision_id="rev-1",
        product_code="P-1",
        rev="A",
        formulation=FormulationData(snapshot={"ingredients": [
            {"weight_pct": 60.0}, {"weight_pct": 40.0}]}, version_id="fv-1"),
        items=[{"name": "x", "pct": 60.0}, {"name": "y", "pct": 40.0}],
        documents=[],
        document_problems=[],
        acceptance=[],
        measured={},
        picks={},
        winning_experiment_id=None,
        waivers={},
    )
    kw.update(over)
    return GateInput(**kw)


def _sds(doc_id, lang, facts, *, validation=None, header=None):
    content = {"meta": {"facts": facts}}
    if validation is not None:
        content["validation"] = validation
    if header is not None:
        content["header"] = header
    return DocumentData(id=doc_id, doc_type="sds", lang=lang, version_no=1,
                        status="approved", batch_id=None, regulation="CN", content=content)


def test_gate_input_module_is_stdlib_only():
    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "app" / "domain" / "gate_input.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert not (node.module or "").startswith("app")
        elif isinstance(node, ast.Import):
            assert all(not a.name.startswith("app") for a in node.names)


def test_evaluate_input_pure_checks_without_any_database():
    gi = _base_input(
        acceptance=[{"metric": "salt_spray_hours", "label": "盐雾", "min": 500}],
        measured={"salt_spray_hours": MeasuredValue(id="m1", value=600.0, unit="h")},
        documents=[
            _sds("d1", "zh-CN", {"un_number": "1993", "properties": {"density_g_cm3": 1.35}},
                 header={"revision_date": "2026-01-01"}),
            _sds("d2", "en-US", {"un_number": "1993", "properties": {"density_g_cm3": 1.35}},
                 header={"revision_date": "2026-01-01"}),
        ],
    )
    result = gate.evaluate_input(gi)
    assert "passed" not in result
    assert result["pure_evaluation"] is True
    assert result["pure_passed"] is True
    assert _check(result, "formula_total")["status"] == "pass"
    assert _check(result, "objectives")["status"] == "pass"
    assert _check(result, "objectives")["items"] == []
    assert _check(result, "documents")["status"] == "pass"
    assert _check(result, "consistency")["status"] == "pass"
    assert _check(result, "sds_label")["status"] == "pass"
    assert _check(result, "required_fields")["status"] == "pass"
    assert _check(result, "data_gaps")["status"] == "not_applicable"
    for key in ("substance_data", "red_lines", "classification_current"):
        assert _check(result, key)["status"] == "not_applicable"


def test_evaluate_input_reports_pure_failures_and_waivers():
    msg = "盐雾：实测 400h 未达到 ≥500"
    waiver_key = ("objectives", "salt_spray_hours", gate.waiver_content_hash(msg))
    gi = _base_input(
        formulation=FormulationData(snapshot={"ingredients": [{"weight_pct": 90.0}]},
                                    version_id="fv-1"),
        acceptance=[{"metric": "salt_spray_hours", "label": "盐雾", "min": 500}],
        measured={"salt_spray_hours": MeasuredValue(id="m1", value=400.0, unit="h")},
        winning_experiment_id="exp-1",
        document_problems=[("tds", "zh-CN", "缺少文档")],
        documents=[
            _sds("d1", "zh-CN", {"un_number": "1993", "properties": {"density_g_cm3": 1.35}}),
        ],
        classification_results={"h": {"data_gaps": [
            {"code": "G1", "message": "缺数据", "severity": "blocking"},
            {"code": "G2", "message": "小缺口", "severity": "info"}]}},
        waivers={waiver_key: WaiverData(id="w1", reason="特批", waived_by="u1",
                                        created_at=datetime(2026, 1, 1))},
    )
    result = gate.evaluate_input(gi)
    assert result["pure_passed"] is False
    assert _check(result, "formula_total")["status"] == "fail"
    objectives = _check(result, "objectives")
    assert objectives["status"] == "waived"
    assert objectives["items"][0]["waived"] is True
    assert objectives["items"][0]["waiver"]["id"] == "w1"
    assert _check(result, "documents")["status"] == "fail"
    assert _check(result, "required_fields")["status"] == "fail"  # SDS without revision_date
    gaps = _check(result, "data_gaps")
    assert gaps["status"] == "fail"
    assert gaps["items"][0]["waivable"] is False  # blocking gap
    assert gaps["items"][1]["waivable"] is True


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _fixture_revision(session):
    t0 = datetime(2026, 1, 1)
    exp = ExperimentRow(domain="surface_treatment", project_id="proj-test", measured={})
    session.add(exp)
    session.flush()
    old = MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp.id, metric="salt_spray_hours",
                         value=600.0, unit="h", review_status="reviewed", created_at=t0)
    new = MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp.id, metric="salt_spray_hours",
                         value=400.0, unit="h", review_status="reviewed", created_at=t0 + timedelta(days=1))
    session.add_all([old, new])
    session.flush()
    session.add(MeasurementPickRow(id=str(uuid.uuid4()), experiment_id=exp.id,
                                   metric="salt_spray_hours", measurement_id=old.id,
                                   picked_by="u1"))
    fv = FormulationVersion(id=str(uuid.uuid4()), lineage_id=str(uuid.uuid4()),
                            domain="surface_treatment",
                            snapshot={"ingredients": [{"name": "a", "weight_pct": 60.0},
                                                      {"name": "b", "weight_pct": 40.0}]})
    session.add(fv)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev="A",
                             formulation_version_id=fv.id, winning_experiment_id=exp.id,
                             spec_snapshot={"acceptance": [{"metric": "salt_spray_hours",
                                                           "label": "盐雾", "min": 500}]})
    session.add(rev)
    session.flush()
    facts = {"un_number": "1993", "properties": {"density_g_cm3": 1.35},
             "pictograms": ["GHS02"], "signal_word": "警告", "h_codes": ["H226"]}
    for lang in ("zh-CN", "en-US"):
        session.add(RegulatoryDocumentRow(
            id=str(uuid.uuid4()), revision_id=rev.id, doc_type="sds", lang=lang,
            regulation="CN", version_no=1, status="approved",
            content={"meta": {"facts": facts}, "header": {"revision_date": "2026-01-01"}}))
    session.flush()
    return rev


# data_gaps is excluded by design: in evaluate() it runs on the GHS
# engine's live results, in evaluate_input() on GateInput's supplied
# classification_results (None here -> not_applicable). The gaps logic
# itself is the same _check_gaps function in both paths (covered above).
PURE_KEYS = ("formula_total", "objectives", "documents", "consistency",
             "sds_label", "required_fields")


def test_adapter_pure_checks_agree_with_evaluate(session):
    rev = _fixture_revision(session)
    full = gate.evaluate(session, rev.id)
    pure = gate.evaluate_input(gate.build_gate_input(session, rev.id))
    assert pure["revision_id"] == rev.id
    for key in PURE_KEYS:
        a, b = _check(full, key), _check(pure, key)
        assert (a["status"], [(i["key"], i["message"], i["waivable"]) for i in a["items"]]) == \
               (b["status"], [(i["key"], i["message"], i["waivable"]) for i in b["items"]]), key


def test_adapter_converts_waivers_to_pure_data(session):
    rev = _fixture_revision(session)
    msg = "盐雾：实测 600h 未达到 ≥700"
    session.add(ReleaseGateWaiverRow(
        id=str(uuid.uuid4()), revision_id=rev.id, check_code="objectives",
        item_key="salt_spray_hours", content_hash=gate.waiver_content_hash(msg),
        reason="特批", waived_by="u1"))
    session.flush()
    gi = gate.build_gate_input(session, rev.id)
    key = ("objectives", "salt_spray_hours", gate.waiver_content_hash(msg))
    assert gi.waivers[key].reason == "特批"
    assert gi.waivers[key].waived_by == "u1"
    assert isinstance(gi.waivers[key].created_at, datetime)
