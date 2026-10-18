"""Regulatory agent (read-only): substance lookup, classification explanation,
hazard-reduction suggestions, GB Q&A — plus the guarantee that ``ask`` never writes.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-reg.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    def sub(session, cas, zh, en, catalog=None):
        s = SubstanceRow(id=str(uuid.uuid4()), cas_no=cas, name_zh=zh, name_en=en, aliases=[],
                         hazardous_catalog_no=catalog)
        session.add(s)
        session.flush()
        return s

    def cls(session, s, hc, cat, h, source="cn_catalog"):
        session.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=s.id, hazard_class=hc,
                                               category=cat, h_code=h, source=source))

    with default_session_factory()() as session:
        hcho = sub(session, "50-00-0", "甲醛", "formaldehyde", 1173)
        cls(session, hcho, "carcinogenicity", "1B", "H350")
        cls(session, hcho, "acute_tox_oral", "3", "H301")
        xylene = sub(session, "1330-20-7", "二甲苯", "xylene", 2289)
        cls(session, xylene, "flammable_liquid", "3", "H226")
        cls(session, xylene, "skin_corrosion", "2", "H315")
        sub(session, "7732-18-5", "水", "water")
        session.commit()
    from app.main import app

    yield TestClient(app)
    get_settings.cache_clear()


def test_ask_substance_by_cas(api):
    r = api.post("/api/plm/regulatory/ask", json={"question": "查一下 50-00-0 的分类"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "substance" and body["tools_used"] == ["lookup_substance"]
    assert body["evidence"][0]["name_zh"] == "甲醛"
    assert "H350" in body["answer"] and "carcinogenicity" in body["answer"]


def test_ask_substance_unknown(api):
    body = api.post("/api/plm/regulatory/ask", json={"question": "查一下 999-99-9"}).json()
    assert body["evidence"] == [] and "未找到" in body["answer"]


def test_ask_explain_returns_trace(api):
    body = api.post("/api/plm/regulatory/ask", json={
        "question": "为什么这个配方分类为易燃液体？请解释分类依据",
        "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60}, {"name": "水", "cas": "7732-18-5", "pct": 40}],
        "properties": {"flash_point_c": 30, "initial_boiling_point_c": 140}}).json()
    assert body["intent"] == "explain"
    assert body["classification"]["classified"] is True
    assert any(e["hazard_class"] == "flammable_liquid" for e in body["evidence"])
    assert "GB 30000" in body["answer"]


def test_ask_explain_without_components(api):
    body = api.post("/api/plm/regulatory/ask", json={"question": "解释分类依据", "intent": "explain"}).json()
    assert "需要提供配方组分" in body["answer"]


def test_ask_substitute_suggests_only_verified_lower(api):
    body = api.post("/api/plm/regulatory/ask", json={
        "question": "怎么降低这个配方的危害？有没有替代组分？",
        "components": [{"name": "甲醛", "cas": "50-00-0", "pct": 5, "role": "preservative"},
                       {"name": "水", "cas": "7732-18-5", "pct": 95}]}).json()
    assert body["intent"] == "substitute"
    assert body["classification"]["classified"] is True
    # The formaldehyde target (carcinogen 1B) is flagged; every alternative carries a verdict.
    assert len(body["suggestions"]) >= 1
    target = body["suggestions"][0]["target"]
    assert target["name"] == "甲醛"
    for alt in body["suggestions"][0]["alternatives"]:
        assert alt["verdict"] in ("lower", "comparable", "unknown")
        assert alt["note"]


def test_ask_standard_without_regulatory_kb(api):
    body = api.post("/api/plm/regulatory/ask", json={"question": "GB 30000.7 对易燃液体怎么分类？"}).json()
    assert body["intent"] == "standard"
    # Empty KB in tests: the agent must say so, not invent an answer.
    assert "未找到" in body["answer"] and body["evidence"] == []


def test_ask_never_writes(api):
    from sqlalchemy import select

    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow

    api.post("/api/plm/regulatory/ask", json={
        "question": "解释分类",
        "components": [{"name": "甲醛", "cas": "50-00-0", "pct": 5}, {"name": "水", "cas": "7732-18-5", "pct": 95}]})
    api.post("/api/plm/regulatory/ask", json={
        "question": "降低危害",
        "components": [{"name": "甲醛", "cas": "50-00-0", "pct": 5}, {"name": "水", "cas": "7732-18-5", "pct": 95}]})
    with default_session_factory()() as session:
        assert session.execute(select(ClassificationResultRow)).scalars().all() == []
