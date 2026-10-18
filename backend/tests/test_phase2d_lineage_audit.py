"""Phase 2D (2-22 / 2-23 / 2-24 / 2-25) end-to-end tests.

- 2-22: build a full chain through the real services (formulation version
  -> winning -> document) and assert the provenance chain is complete.
- 2-23: the product-level ``/product-chain`` view returns the whole chain.
- 2-24: the ``sqlite`` backend name is a deprecated alias of ``native``.
- 2-25: the audit middleware records mutations even when the endpoint
  itself writes no audit entry; reads are not audited; a middleware
  failure never breaks the request.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import provenance as provenance_api
from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import ExperimentRow
from app.db.models_plm import AuditLogRow
from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.middleware.audit import AuditMiddleware
from app.services import provenance as prov
from app.services.formulation_history import FormulationHistoryStore
from app.services.identity.rbac import PERMISSIONS, Principal
from app.services.plm.documents import service as docs
from app.services.plm import winning as winning_svc


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/phase2d.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def _form() -> Formulation:
    return Formulation(
        name="Primer",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name="Epoxy", role="resin", weight_pct=60.0)],
        predicted={},
    )


def _build_chain(sf):
    """formulation v1 -> v2 -> winning revision -> SDS document. Returns ids."""
    with sf() as s:
        prod = docs.create_product(s, code="P-2D", name_zh="2D测试产品")
        s.commit()
        product_id = prod.id
    store = FormulationHistoryStore(sf)
    v1 = store.save_version(_form(), created_by="u1", project_id="proj-1")
    v2 = store.save_version(_form(), parent_version_id=v1.id, created_by="u1", project_id="proj-1")
    with sf() as s:
        exp = ExperimentRow(domain="anticorrosion_coating", project_id="proj-1", factors={}, measured={}, source="manual")
        s.add(exp)
        s.commit()
        experiment_id = exp.id
    principal = Principal(user_id="u1", global_permissions=frozenset(PERMISSIONS), project_permissions={})
    with sf() as s:
        rev, _warnings = winning_svc.select_winning(
            s, product_id=product_id, formulation_version_id=v2.id,
            experiment_id=experiment_id, user_id="u1", principal=principal,
        )
        s.commit()
        revision_id = rev.id
    with sf() as s:
        doc = docs.generate_document(s, revision_id, "sds", lang="zh-CN", payload={}, created_by="u1")
        s.commit()
        document_id = doc.id
    return {
        "product_id": product_id,
        "v1": v1.id,
        "v2": v2.id,
        "experiment_id": str(experiment_id),
        "revision_id": revision_id,
        "document_id": document_id,
        "lineage_id": v1.lineage_id,
    }


def _edge_set(edges) -> set[tuple[str, str, str, str, str]]:
    return {(e["from_type"], e["from_id"], e["to_type"], e["to_id"], e["relation"]) for e in edges}


# ── 2-22: full-chain lineage ────────────────────────────────────────────────


def test_full_chain_lineage_from_document(sf):
    ids = _build_chain(sf)
    edges = _edge_set(prov.lineage("document", ids["document_id"], depth=10, session_factory=sf))
    assert ("product_revision", ids["revision_id"], "document", ids["document_id"], "documented_as") in edges
    assert ("product", ids["product_id"], "product_revision", ids["revision_id"], "has_revision") in edges
    assert ("experiment", ids["experiment_id"], "product_revision", ids["revision_id"], "selected_as_winning") in edges
    assert ("formulation_version", ids["v2"], "product_revision", ids["revision_id"], "frozen_as") in edges
    assert ("formulation", ids["lineage_id"], "formulation_version", ids["v2"], "has_version") in edges
    assert ("formulation_version", ids["v1"], "formulation_version", ids["v2"], "versioned_to") in edges
    assert ("project", "proj-1", "formulation_version", ids["v2"], "has_formulation") in edges


def test_downstream_from_product_reaches_the_document(sf):
    ids = _build_chain(sf)
    edges = _edge_set(prov.downstream("product", ids["product_id"], depth=4, session_factory=sf))
    assert ("product", ids["product_id"], "product_revision", ids["revision_id"], "has_revision") in edges
    assert ("product_revision", ids["revision_id"], "document", ids["document_id"], "documented_as") in edges


def test_in_session_hook_failure_does_not_break_the_business_write(sf):
    """A provenance DB error inside a service transaction is fail-open:
    the business write still commits (the edge is lost, the write is not)."""
    with sf() as session:
        prod = docs.create_product(session, code="P-2D-FAIL", name_zh="fail-open")
        session.commit()
        product_id = prod.id
    # Break the edge table so the hook's SAVEPOINT rolls back, then repair.
    from app.db.models_plm import LineageEdgeRow

    with sf() as session:
        LineageEdgeRow.__table__.drop(bind=session.get_bind())
        session.commit()
    with sf() as session:
        # create_revision's hook fails (table gone) but the revision is still created.
        rev = docs.create_revision(session, product_id, created_by="u1")
        session.commit()
        assert rev.id
    # Repair + prove the business row survived.
    prov.ensure_provenance(sf)
    from app.db.models_plm import ProductRevisionRow

    with sf() as session:
        assert session.get(ProductRevisionRow, rev.id) is not None


# ── 2-23: product-chain endpoint ────────────────────────────────────────────


def test_product_chain_returns_the_whole_chain(sf, monkeypatch):
    ids = _build_chain(sf)
    monkeypatch.setattr(provenance_api, "default_session_factory", lambda: sf)
    body = provenance_api.get_product_chain(product_id=ids["product_id"])
    assert body["product_id"] == ids["product_id"]
    node_keys = {(n["node_type"], n["node_id"]) for n in body["nodes"]}
    assert ("product", ids["product_id"]) in node_keys
    assert ("product_revision", ids["revision_id"]) in node_keys
    assert ("document", ids["document_id"]) in node_keys
    assert ("experiment", ids["experiment_id"]) in node_keys
    assert ("formulation_version", ids["v2"]) in node_keys
    edges = _edge_set(body["edges"])
    assert ("product_revision", ids["revision_id"], "document", ids["document_id"], "documented_as") in edges
    assert ("experiment", ids["experiment_id"], "product_revision", ids["revision_id"], "selected_as_winning") in edges
    stages = {n["node_type"]: n["stage"] for n in body["nodes"]}
    assert stages["document"] == "plm"
    assert stages["experiment"] == "doe"


def test_product_chain_404_for_unknown_product(sf, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(provenance_api, "default_session_factory", lambda: sf)
    with pytest.raises(HTTPException, match="产品不存在"):
        provenance_api.get_product_chain(product_id="no-such-product")


# ── 2-24: sqlite backend name is a deprecated alias of native ──────────────


def test_sqlite_backend_alias_resolves_to_native_stores(monkeypatch):
    from app.config import Settings
    from app.db import campaign_store, store as experiment_store

    campaign_store.reset_campaign_store()
    experiment_store.reset_experiment_store()
    try:
        cs = campaign_store.get_campaign_store(Settings(campaign_backend="sqlite"))
        assert type(cs).__name__ == "LocalCampaignStore"
        es = experiment_store.get_experiment_store(Settings(experiment_backend="sqlite"))
        assert type(es).__name__ == "LocalExperimentStore"
    finally:
        campaign_store.reset_campaign_store()
        experiment_store.reset_experiment_store()


# ── 2-25: audit middleware ──────────────────────────────────────────────────


@pytest.fixture()
def audit_client(sf, monkeypatch):
    import app.db.database as database_mod

    monkeypatch.setattr(database_mod, "default_session_factory", lambda: sf)
    app = FastAPI()
    app.add_middleware(AuditMiddleware)

    @app.post("/api/things")
    def _create_thing():
        return {"ok": True}

    @app.get("/api/things")
    def _list_things():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def _audit_rows(sf):
    with sf() as session:
        return session.query(AuditLogRow).order_by(AuditLogRow.seq).all()


def test_middleware_audits_mutations_without_service_help(audit_client, sf):
    r = audit_client.post("/api/things", json={})
    assert r.status_code == 200
    rows = _audit_rows(sf)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "http.POST"
    assert row.entity_type == "http"
    assert row.entity_id == "/api/things"
    assert row.detail["method"] == "POST"
    assert row.detail["path"] == "/api/things"
    assert row.detail["status_code"] == 200


def test_middleware_does_not_audit_reads(audit_client, sf):
    assert audit_client.get("/api/things").status_code == 200
    assert audit_client.post("/api/things", json={}).status_code == 200
    assert audit_client.get("/api/things").status_code == 200
    assert len(_audit_rows(sf)) == 1  # only the POST


def test_middleware_never_logs_query_strings(audit_client, sf):
    r = audit_client.post("/api/things?token=secret-value&next=/x", json={})
    assert r.status_code == 200
    rows = _audit_rows(sf)
    assert len(rows) == 1
    assert "secret-value" not in str(rows[0].detail)
    assert "token" not in str(rows[0].detail)


def test_middleware_failure_never_breaks_the_request(audit_client, sf, monkeypatch):
    import app.services.audit as audit_svc

    monkeypatch.setattr(audit_svc.hash_chain, "append", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit db gone")))
    r = audit_client.post("/api/things", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_audit_middleware_is_registered_on_the_real_app():
    from app.main import app

    assert any(m.cls is AuditMiddleware for m in app.user_middleware)
