"""F4 backend: list endpoints for the web UI, revision spec editing, label designer layouts."""
from __future__ import annotations

import pytest

from tests.test_plm_release import PASSWORD, PROPS, _ready, env  # noqa: F401  (fixture)

LAYOUT = {"size_mm": [100, 150], "elements": [
    {"id": "name", "type": "product_name", "x": 4, "y": 4, "w": 92, "h": 10, "font_size": 11, "align": "center", "bold": True},
    {"id": "pic", "type": "pictograms", "x": 4, "y": 16, "w": 92, "h": 22, "align": "center"},
    {"id": "sw", "type": "signal_word", "x": 4, "y": 40, "w": 92, "h": 8, "font_size": 12, "align": "center"},
    {"id": "h", "type": "hazard_statements", "x": 4, "y": 50, "w": 92, "h": 20, "font_size": 7},
    {"id": "p", "type": "precautionary_statements", "x": 4, "y": 72, "w": 92, "h": 40, "font_size": 7},
    {"id": "sup", "type": "supplier", "x": 4, "y": 114, "w": 70, "h": 14, "font_size": 6},
    {"id": "tel", "type": "emergency_phone", "x": 4, "y": 130, "w": 70, "h": 6, "font_size": 6},
    {"id": "batch", "type": "text", "x": 4, "y": 138, "w": 70, "h": 6, "font_size": 6, "text": "批号：{{batch_no}}"},
    {"id": "qr", "type": "qr", "x": 78, "y": 120, "w": 18, "h": 18},
]}


def test_lists_revision_detail_and_spec_editing(env):  # noqa: F811
    c, h, rid, pid = env["client"], env["h"], env["rid"], env["pid"]
    products = c.get("/api/plm/products").json()
    assert products[0]["code"] == "FX-100" and products[0]["revisions"] == 1
    assert c.get("/api/plm/products?q=nomatch").json() == []
    rev = c.get(f"/api/plm/revisions/{rid}").json()
    assert rev["rev"] == "A" and rev["product"]["id"] == pid and rev["spec_snapshot"]["release_specs"]
    spec = {**rev["spec_snapshot"], "acceptance": [{"metric": "salt_spray_hours", "min": 480}]}
    assert c.put(f"/api/plm/revisions/{rid}/spec", headers=h["qa"], json={"spec_snapshot": spec}).status_code == 403
    r = c.put(f"/api/plm/revisions/{rid}/spec", headers=h["lead"], json={"spec_snapshot": spec})
    assert r.status_code == 200 and r.json()["spec_snapshot"]["acceptance"][0]["min"] == 480

    _ready(env)
    c.put(f"/api/plm/revisions/{rid}/spec", headers=h["lead"], json={"spec_snapshot": rev["spec_snapshot"]})
    pkg = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).json()
    pending = c.get("/api/plm/packages?status=under_review").json()
    assert pending[0]["id"] == pkg["id"] and pending[0]["current_step"] == {"seq": 1, "role": "project_lead"}
    assert pending[0]["product_code"] == "FX-100" and pending[0]["rev"] == "A"
    assert c.put(f"/api/plm/revisions/{rid}/spec", headers=h["lead"], json={"spec_snapshot": spec}).status_code == 409


def test_label_layouts_validate_render_and_drive_generation(env):  # noqa: F811
    c, h, rid = env["client"], env["h"], env["rid"]
    bad = {"size_mm": [100, 150], "elements": [{"type": "pictograms", "x": 90, "y": 10, "w": 20, "h": 20}]}
    assert c.post("/api/plm/label-templates", headers=h["ehs"], json={"name": "x", "layout": bad}).status_code == 422
    assert c.post("/api/plm/label-templates", headers=h["qa"], json={"name": "x", "layout": LAYOUT}).status_code == 403

    preview = c.post("/api/plm/label-templates/preview", json={"layout": LAYOUT, "variables": {"batch_no": "B1"}})
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")
    assert preview.headers["X-Label-Missing"] == "" and preview.headers["X-Label-Overflow"] == ""
    tiny = {**LAYOUT, "elements": [{**e, "h": 3} if e["id"] == "p" else e for e in LAYOUT["elements"]]}
    assert c.post("/api/plm/label-templates/preview", json={"layout": tiny}).headers["X-Label-Overflow"] == "p"
    partial = {**LAYOUT, "elements": LAYOUT["elements"][:2]}
    assert "emergency_phone" in c.post("/api/plm/label-templates/preview", json={"layout": partial}).headers["X-Label-Missing"]

    r = c.post("/api/plm/label-templates", headers=h["ehs"], json={"name": "18L 桶标", "layout": {**LAYOUT, "size_mm": [150, 200]}
                                                                     | {"elements": LAYOUT["elements"]}})
    assert r.status_code == 201 and r.json()["is_default"]
    tid = r.json()["id"]
    assert c.get(f"/api/plm/label-templates/{tid}").json()["layout"]["size_mm"] == [150, 200]
    assert [t["id"] for t in c.get("/api/plm/label-templates").json()] == [tid]

    lab = c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={
        "doc_type": "label", "properties": PROPS, "label": {"container_volume_l": 18, "variables": {"batch_no": "B2610"}}}).json()
    assert lab["content"]["meta"]["layout_template_id"] == tid
    assert lab["content"]["meta"]["size"]["width_mm"] == 150 and lab["content"]["meta"]["size"]["ok"] is True
    assert lab["content"]["meta"]["variables"] == {"batch_no": "B2610"}
    pdf = c.get(f"/api/plm/documents/{lab['id']}/file/pdf").content
    assert pdf.startswith(b"%PDF")
    real = c.post("/api/plm/label-templates/preview", json={"layout": LAYOUT, "document_id": lab["id"]})
    assert real.status_code == 200


@pytest.mark.parametrize("field,value", [("type", "nope"), ("w", -1)])
def test_layout_validation_errors(field, value):
    from app.services.plm.render.label_layout import LayoutError, validate_layout

    e = {"type": "text", "x": 1, "y": 1, "w": 10, "h": 5, field: value}
    with pytest.raises(LayoutError):
        validate_layout({"size_mm": [100, 100], "elements": [e]})
