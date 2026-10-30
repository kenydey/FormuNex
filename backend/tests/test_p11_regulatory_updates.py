"""P1-1: regulatory update packages (stage -> EHS approve -> apply -> rollback).

Covers the approval gate (draft releases invisible to latest_data_release),
seed source-version recording, CTX enrichment merging (additive only), CLP
Annex VI merging with rule-set-dependent source priority, the full package
lifecycle with rollback, CLP staging validation, and the SDS version line.
"""

from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select

import app.db.models  # noqa: F401  (registers every table)
from app.db.models import Base
from app.db.models_regulatory import (
    RegulatoryDataRelease,
    SubstanceClassificationRow,
    SubstanceRow,
)
from app.services.plm.documents.sds_builder import SdsContext, build_sds
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties
from app.services.plm.ghs.service import latest_data_release
from app.services.plm.regulatory import updates as reg_updates
from app.services.plm.regulatory.seed import seed_regulatory_db
from app.services.plm.regulatory.source_priority import effective


def _write(path, name, payload):
    p = path / name
    if name.endswith(".gz"):
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def resources(tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    _write(res, "common_chemicals.json.gz", {
        "source": "MSDS-Generator chemical_database/common_chemicals.json",
        "version": "1.0", "last_updated": "2026-02-08T13:46:30",
        "rows": [
            ["64-17-5", "乙醇", "Ethanol", "C2H6O"],
            ["108-95-2", "苯酚", "Phenol", "C6H6O"],
            ["7732-18-5", "水", "Water", ""],
        ]})
    _write(res, "hazard_chemical_info.json", {"chemicals": [
        {"index": 2568, "chinese_name": "乙醇", "alias": "酒精", "english_name": "ethanol",
         "cas_number": "64-17-5", "hazard_category": "易燃液体,类别 2", "notes": ""},
        {"index": 1, "chinese_name": "苯酚", "alias": "", "english_name": "phenol",
         "cas_number": "108-95-2", "hazard_category": "急性毒性-经口,类别 3", "notes": ""},
    ]})
    _write(res, "oel_gbz2_2019.json", {"records": []})
    _write(res, "oel_eh40.json", {"records": []})
    _write(res, "hazard_statements.json", {"statements": [
        {"code": "H225", "statement": "高度易燃液体和蒸气", "hazard_type": "易燃液体", "hazard_category": "类别 2"},
    ]})
    _write(res, "precaution_statements.json", {"statements": []})
    _write(res, "gb12268_2025.json", {"entries": {}})
    _write(res, "hpstatements_en.json", {"statements": {}})
    return res


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'reg.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def packages(tmp_path, monkeypatch):
    pkg = tmp_path / "packages"
    pkg.mkdir()
    monkeypatch.setattr(reg_updates, "PACKAGES_DIR", pkg)
    return pkg


def _principal():
    return SimpleNamespace(can=lambda p: p == "regulatory.review", user_id="ehs-1")


def _seed(engine, resources, **kw):
    result = seed_regulatory_db(engine, resources=resources, **kw)
    assert not result["skipped"], result
    return result


def _row(session, model, *where):
    return session.execute(select(model).where(*where)).scalar_one()


# ── approval gate + source versions ───────────────────────────────────────────

def test_latest_returns_only_approved(engine, resources):
    r1 = _seed(engine, resources)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert latest_data_release(session) == r1["release_id"]
        session.add(RegulatoryDataRelease(
            id="draft-1", label="draft", source="seed", stats={},
            input_digest="x", status="draft",
        ))
        session.commit()
        assert latest_data_release(session) == r1["release_id"]


def test_seed_records_source_versions(engine, resources):
    result = _seed(engine, resources)
    sources = result["stats"]["sources"]
    assert sources["common_chemicals.json.gz"]["version"] == "1.0"
    assert sources["common_chemicals.json.gz"]["last_updated"] == "2026-02-08T13:46:30"


# ── CTX enrichment (additive only) ────────────────────────────────────────────

def test_ctx_enrichment_merge_is_additive(engine, resources):
    _write(resources, "ctx_enrichment.json", {"entries": {
        "64-17-5": {"dtxsid": "DTXSID7020582", "synonyms": ["酒精", "Ethyl alcohol"], "mf": "C9H99"},
        "7732-18-5": {"dtxsid": "DTXSID7020182", "synonyms": ["Water"], "mf": "H2O"},
        "999-99-9": {"synonyms": ["Ghost"], "mf": "X"},
    }})
    result = _seed(engine, resources, extra_files=["ctx_enrichment.json"])
    assert result["stats"]["ctx_enrichment"] == {"merged": 2, "skipped_unknown": 1}
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        ethanol = _row(session, SubstanceRow, SubstanceRow.cas_no == "64-17-5")
        assert ethanol.aliases == ["酒精", "Ethyl alcohol"]
        assert ethanol.mf == "C2H6O"  # curated value never overwritten
        water = _row(session, SubstanceRow, SubstanceRow.cas_no == "7732-18-5")
        assert water.mf == "H2O"  # missing value filled
        assert water.aliases == ["Water"]


# ── CLP merge + source priority ───────────────────────────────────────────────

def test_clp_merge_and_priority(engine, resources):
    _write(resources, "clp_atp.json", {
        "atp": "ATP21", "regulation": "Regulation (EU) 2024/197", "applies_from": "2025-09-01",
        "entries": [
            {"cas": "64-17-5", "index_no": "603-002-00-5", "hazard_class": "Flam. Liq.",
             "category": "2", "h_code": "H225"},
            {"cas": "108-95-2", "hazard_class": "Acute Tox.", "category": "3", "h_code": "H301"},
            {"cas": "108-95-2", "hazard_class": "No Such Class", "category": "1"},
            {"cas": "999-99-9", "hazard_class": "Flam. Liq.", "category": "2"},
        ]})
    result = _seed(engine, resources, extra_files=["clp_atp.json"])
    assert result["stats"]["clp_atp"]["inserted"] == 2
    assert result["stats"]["clp_atp"]["skipped"] == 2
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        rows = session.execute(
            select(SubstanceClassificationRow).where(
                SubstanceClassificationRow.source == "clp_annex_vi")
        ).scalars().all()
        by_cas_class = {}
        for r in rows:
            sub = session.get(SubstanceRow, r.substance_id)
            by_cas_class[(sub.cas_no, r.hazard_class)] = r
        assert by_cas_class[("64-17-5", "flammable_liquid")].category == "2"
        assert by_cas_class[("108-95-2", "acute_tox_oral")].category == "3"
        assert by_cas_class[("64-17-5", "flammable_liquid")].source_ref.startswith("CLP Annex VI ATP21")

    def _fake(source):
        return SimpleNamespace(hazard_class="flammable_liquid", source=source, locked_at=None)

    # Default order: the China catalogue outranks CLP (gap-fill only).
    assert [r.source for r in effective([_fake("clp_annex_vi"), _fake("cn_catalog")])] == ["cn_catalog"]
    # EU order: harmonised CLP outranks the catalogue.
    eu_order = ["manual", "clp_annex_vi", "supplier_sds", "cn_catalog", "pubchem", "inferred"]
    assert [r.source for r in effective([_fake("clp_annex_vi"), _fake("cn_catalog")], eu_order)] == ["clp_annex_vi"]


# ── package lifecycle ─────────────────────────────────────────────────────────

def test_package_approve_and_rollback(engine, resources, packages):
    r1 = _seed(engine, resources)
    manifest = reg_updates.stage_clp_package(
        atp="ATP21", regulation="Regulation (EU) 2024/197", applies_from="2025-09-01",
        entries=[{"cas": "64-17-5", "index_no": "603-002-00-5", "hazard_class": "Flam. Liq.",
                  "category": "2", "h_code": "H225"}],
        created_by="ehs-1",
    )
    assert manifest["status"] == "draft"
    # Staging alone changes nothing.
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert latest_data_release(session) == r1["release_id"]
        assert session.execute(
            select(SubstanceClassificationRow).where(
                SubstanceClassificationRow.source == "clp_annex_vi")
        ).scalars().all() == []

    out = reg_updates.approve_package(engine, manifest["package_id"], _principal(), resources_dir=resources)
    assert out["manifest"]["status"] == "approved"
    assert (resources / "clp_atp.json").exists()
    with Session(engine) as session:
        assert latest_data_release(session) == out["manifest"]["applied_release_id"]
        assert session.execute(
            select(SubstanceClassificationRow).where(
                SubstanceClassificationRow.source == "clp_annex_vi")
        ).scalars().all() != []

    rolled = reg_updates.rollback_release(engine, out["manifest"]["applied_release_id"], _principal(), resources_dir=resources)
    with Session(engine) as session:
        assert session.execute(
            select(SubstanceClassificationRow).where(
                SubstanceClassificationRow.source == "clp_annex_vi")
        ).scalars().all() == []
        # Rollback is itself a traced release (not a silent return to R1).
        assert latest_data_release(session) == rolled["release_id"]
        rolled_row = session.get(RegulatoryDataRelease, rolled["release_id"])
        assert rolled_row.stats["rollback_of"] == out["manifest"]["applied_release_id"]
    assert not (resources / "clp_atp.json").exists()


def test_rollback_refuses_tampered_live_files(engine, resources, packages):
    _seed(engine, resources)
    manifest = reg_updates.stage_clp_package(
        atp="ATP21", regulation="Regulation (EU) 2024/197", applies_from="2025-09-01",
        entries=[{"cas": "64-17-5", "hazard_class": "Flam. Liq.", "category": "2", "h_code": "H225"}],
        created_by="ehs-1",
    )
    out = reg_updates.approve_package(engine, manifest["package_id"], _principal(), resources_dir=resources)
    (resources / "clp_atp.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(reg_updates.UpdateError, match="手动修改"):
        reg_updates.rollback_release(engine, out["manifest"]["applied_release_id"], _principal(), resources_dir=resources)


def test_clp_stage_validation(engine, resources, packages):
    with pytest.raises(reg_updates.UpdateError, match="全部无效"):
        reg_updates.stage_clp_package(
            atp="ATP99", regulation="X", applies_from="", entries=[
                {"cas": "64-17-5", "hazard_class": "Nope", "category": "1"},
            ], created_by="ehs-1")
    manifest = reg_updates.stage_clp_package(
        atp="ATP21", regulation="Regulation (EU) 2024/197", applies_from="2025-09-01",
        entries=[
            {"cas": "64-17-5", "hazard_class": "Flam. Liq.", "category": "2", "h_code": "H225"},
            {"cas": "64-17-5", "hazard_class": "Acute Tox.", "category": "4"},
        ],
        created_by="ehs-1",
    )
    assert manifest["report"]["clp_validation"]["accepted"] == 1
    assert manifest["report"]["clp_validation"]["rejected"] == 1


def test_reject_package(engine, resources, packages):
    manifest = reg_updates.stage_clp_package(
        atp="ATP21", regulation="Regulation (EU) 2024/197", applies_from="2025-09-01",
        entries=[{"cas": "64-17-5", "hazard_class": "Flam. Liq.", "category": "2", "h_code": "H225"}],
        created_by="ehs-1",
    )
    rejected = reg_updates.reject_package(manifest["package_id"], _principal())
    assert rejected["status"] == "rejected"
    with pytest.raises(reg_updates.UpdateError, match="不可批准"):
        reg_updates.approve_package(engine, manifest["package_id"], _principal(), resources_dir=resources)


# ── SDS version line ──────────────────────────────────────────────────────────

def _sds_doc(**kw):
    comps = [Component("二甲苯", 25, "1330-20-7",
                       hazards=[ComponentHazard("flammable_liquid", "3", "H226")])]
    cls = classify(comps, MixtureProperties(flash_point_c=27)).to_dict()
    cls["statements"] = {c: {"zh": f"{c}中文", "en": ""} for c in cls["h_codes"] + cls["p_codes"]}
    ctx = SdsContext(
        product={"name_zh": "测试品", "name_en": "Test", "code": "T-1"},
        company={"name": "测试公司"}, classification=cls, statements=cls["statements"],
        components=cls["disclosure"], revision={"version": "A.1", "date": "2026-10-31"}, **kw)
    return build_sds(ctx)


def test_sds_renders_data_release_line():
    doc = _sds_doc(data_release={
        "id": "rel-1", "label": "update-abc",
        "sources": {"common_chemicals.json.gz": {"version": "1.0"}},
    })
    assert doc["meta"]["data_release_id"] == "rel-1"
    fields = {f["key"]: f for f in doc["sections"][15]["fields"]}
    assert "data_release" in fields
    assert "update-abc" in fields["data_release"]["value"]
    assert "1.0" in fields["data_release"]["value"]


def test_sds_without_data_release_renders_no_line():
    doc = _sds_doc()
    assert doc["meta"]["data_release_id"] is None
    fields = {f["key"]: f for f in doc["sections"][15]["fields"]}
    assert "data_release" not in fields


# ── migration file sanity ─────────────────────────────────────────────────────

def test_migration_0076_fields_exist():
    assert "status" in RegulatoryDataRelease.__table__.c
    assert "approved_by" in RegulatoryDataRelease.__table__.c
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m0076", "app/db/alembic/versions/0076_regulatory_release_approval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0076" and module.down_revision == "0075"


# ── EPA CTX client (documented Chemical API 1.1.1 shape) ─────────────────────

def _ctx_transport():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        cas = request.url.path.rsplit("/", 1)[-1]
        if cas == "64-17-5":
            return httpx.Response(200, json={
                "searchName": "Ethanol", "preferredName": "Ethanol", "isMarkush": False,
                "hasStructureImage": 1, "dtxcid": "DTXCID7020582", "casrn": "64-17-5",
                "dtxsid": "DTXSID7020582", "smiles": "CCO", "rank": 1, "searchValue": "64-17-5",
            })
        if cas == "7732-18-5":
            return httpx.Response(200, json={
                "preferredName": "Water", "casrn": "7732-18-5",
                "dtxsid": "DTXSID7020182", "smiles": "O",
            })
        if cas == "999-99-9":
            return httpx.Response(404, json={})
        if cas == "111-11-1":
            return httpx.Response(401, json={})
        if cas == "222-22-2":
            return httpx.Response(429, json={})
        raise httpx.ConnectError("boom", request=request)

    return httpx.MockTransport(handler)


def test_ctx_fetch_maps_documented_fields():
    import httpx

    from app.services.plm.regulatory import ctx_client

    client = httpx.Client(transport=_ctx_transport())
    entry = ctx_client.fetch_by_cas(client, "64-17-5", "key")
    assert entry["dtxsid"] == "DTXSID7020582"
    assert entry["dtxcid"] == "DTXCID7020582"
    assert entry["preferred_name"] == "Ethanol"
    assert entry["smiles"] == "CCO"
    # The search endpoint has no formula / synonyms fields: nothing invented.
    assert "mf" not in entry and "synonyms" not in entry
    assert ctx_client.fetch_by_cas(client, "999-99-9", "key") is None
    with pytest.raises(ctx_client.CtxAuthError):
        ctx_client.fetch_by_cas(client, "111-11-1", "key")
    with pytest.raises(ctx_client.CtxRateLimited):
        ctx_client.fetch_by_cas(client, "222-22-2", "key")


def test_ctx_build_package_counts_and_stages(packages):
    import httpx

    from app.services.plm.regulatory import ctx_client
    from app.services.plm.regulatory import updates as reg_updates

    client = httpx.Client(transport=_ctx_transport())
    manifest = ctx_client.build_ctx_package(
        ["64-17-5", "7732-18-5", "999-99-9", "333-33-3"],
        created_by="ehs-1", api_key="key", client=client,
    )
    assert manifest["status"] == "draft"
    assert manifest["report"]["fetched"] == 2
    assert manifest["report"]["not_found"] == ["999-99-9"]
    assert len(manifest["report"]["errors"]) == 1
    stored = reg_updates.get_package(manifest["package_id"])
    assert stored["files"] == ["ctx_enrichment.json"]


def test_ctx_build_package_refuses_empty(packages):
    import httpx

    from app.services.plm.regulatory import ctx_client
    from app.services.plm.regulatory import updates as reg_updates

    client = httpx.Client(transport=_ctx_transport())
    with pytest.raises(reg_updates.UpdateError, match="未取回任何物质"):
        ctx_client.build_ctx_package(["999-99-9"], created_by="ehs-1", api_key="key", client=client)


def test_ctx_enrichment_fills_missing_smiles(engine, resources):
    _write(resources, "ctx_enrichment.json", {"entries": {
        "7732-18-5": {"dtxsid": "DTXSID7020182", "smiles": "O"},
    }})
    result = _seed(engine, resources, extra_files=["ctx_enrichment.json"])
    assert result["stats"]["ctx_enrichment"]["merged"] == 1
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        water = _row(session, SubstanceRow, SubstanceRow.cas_no == "7732-18-5")
        assert water.smiles == "O"
