"""POST /api/formulations/export/pdf — one leaderboard card rendered to PDF."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

_FORM = {
    "name": "硅烷底漆 A",
    "domain": "anticorrosion_coating",
    "ingredients": [
        {"name": "KH-550", "role": "coupling_agent", "cas_no": "919-30-2", "weight_pct": 5.0},
        {"name": "Water", "zh_name": "去离子水", "role": "solvent", "cas_no": "7732-18-5", "weight_pct": 95.0},
    ],
    "rationale": "硅烷偶联剂提高附着力。",
    "predicted": {"salt_spray_h": 720.0},
    "predicted_std": {"salt_spray_h": 48.0},
    "score": 0.82,
    "warnings": ["CAS 未核验"],
}


def test_export_pdf_returns_pdf():
    r = TestClient(app).post("/api/formulations/export/pdf", json={"formulation": _FORM})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and "filename*=UTF-8''" in cd
    assert r.headers["x-font-embedded"] in ("0", "1")


def test_export_pdf_english_and_minimal_formulation():
    form = {"name": "F/1", "domain": "degreaser", "ingredients": []}
    r = TestClient(app).post("/api/formulations/export/pdf", json={"formulation": form, "lang": "en-US", "title": "Card 1"})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    assert 'filename="Card_1.pdf"' in r.headers["content-disposition"]


def test_export_pdf_validates_body():
    c = TestClient(app)
    assert c.post("/api/formulations/export/pdf", json={}).status_code == 422
    assert c.post("/api/formulations/export/pdf", json={"formulation": _FORM, "lang": "fr"}).status_code == 422


def test_export_pdf_in_openapi():
    spec = TestClient(app).get("/openapi.json").json()
    assert "/api/formulations/export/pdf" in spec["paths"]
