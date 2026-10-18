"""补齐计划 Phase 3B（3-16）：已发布文档版本不可变核实。

不可变 = 内容永不原地修改：生成只建新版本行；签批只改 status（draft →
approved → released / superseded），不碰 content；API 上不存在任何修改
文档内容的路由。
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """PLM API fixture (same setup as test_phase3b_render.py, separate DB)."""
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'docs3b-imm.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "方枢示例化工有限公司")
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_ADDRESS", "上海市示例路 1 号")
    monkeypatch.setenv("FORMUNEX_PLM_EMERGENCY_PHONE", "021-0001")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceOELRow, SubstanceRow

    with default_session_factory()() as s:
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                           hazardous_catalog_no=2289)
        s.add(xyl)
        s.add(SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[]))
        s.flush()
        for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
            s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                             h_code=h, source="cn_catalog"))
        s.add(SubstanceOELRow(id=str(uuid.uuid4()), substance_id=xyl.id, pc_twa=50, pc_stel=100))
        s.add(FormulationVersion(id="fv-1", lineage_id="l", version=1, domain="coating", name="v1", created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30},
                                                           {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70}]}))
        s.commit()
    from app.main import app

    client = TestClient(app)
    pid = client.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100 环氧底漆"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1",
        "spec_snapshot": {"release_specs": [{"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60},
                                            {"metric": "visc", "label": "黏度", "unit": "s", "max": 90}]}}).json()["id"]
    yield client, pid, rid
    get_settings.cache_clear()


def test_no_content_mutation_routes():
    from app.api.plm import router

    mutating = {"PUT", "PATCH", "DELETE"}
    # 只查文档 / 模板 / 标签路由：PUT /revisions/{id}/spec 是规格契约（非文档内容），不在本测试范围内。
    in_scope = ("documents", "templates", "labels")
    for route in router.routes:
        if not any(s in route.path for s in in_scope):
            continue
        methods = getattr(route, "methods", set()) or set()
        assert not (methods & mutating), (route.path, methods)
    posts = sorted(r.path for r in router.routes
                   if "POST" in (getattr(r, "methods", set()) or set()) and "documents" in r.path)
    # 生成 / 上传 / 预览 / 签批：没有一条是“修改已有文档内容”。
    assert posts == ["/plm/documents/{document_id}/approve",
                     "/plm/revisions/{revision_id}/documents"], posts


def test_regenerate_never_edits_existing_version(api):  # noqa: F811
    client, _pid, rid = api
    payload = {"doc_type": "sds", "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}}
    first = client.post(f"/api/plm/revisions/{rid}/documents", json=payload).json()
    assert first["version_no"] == 1
    snapshot = copy.deepcopy(client.get(f"/api/plm/documents/{first['id']}").json())

    second = client.post(f"/api/plm/revisions/{rid}/documents", json=payload).json()
    assert second["id"] != first["id"] and second["version_no"] == 2
    assert second["supersedes_id"] == first["id"]

    again = client.get(f"/api/plm/documents/{first['id']}").json()
    assert again["content_hash"] == snapshot["content_hash"] == first["content_hash"]
    assert again["content"] == snapshot["content"]  # 旧版本内容一字未动
    assert again["status"] == "draft"  # 重新生成不改旧版本状态


def test_frozen_revision_cannot_regenerate(api):  # noqa: F811
    from datetime import datetime

    from app.db.database import default_session_factory
    from app.db.models_plm import ProductRevisionRow

    client, _pid, rid = api
    doc = client.post(f"/api/plm/revisions/{rid}/documents",
                      json={"doc_type": "sds", "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}}).json()
    with default_session_factory()() as s:
        s.get(ProductRevisionRow, rid).frozen_at = datetime(2026, 10, 16)
        s.commit()
    r = client.post(f"/api/plm/revisions/{rid}/documents",
                    json={"doc_type": "sds", "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}})
    assert r.status_code == 422 and "冻结" in r.json()["detail"]
    assert client.get(f"/api/plm/documents/{doc['id']}").json()["content_hash"] == doc["content_hash"]
