"""M5: accounts & sessions, release gate, e-signatures, 4-step sign-off, delivery package, connectors."""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime

import httpx
import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")
pytest.importorskip("svglib")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

PASSWORD = "Fx-Release-2026"
USERS = {
    "rd": ["rd_engineer"], "lead": ["project_lead"], "ehs": ["ehs_regulatory"], "qa": ["quality"],
    "rm": ["release_manager"], "admin": ["sys_admin"],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'release.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "方枢示例化工有限公司")
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_ADDRESS", "上海市示例路 1 号")
    monkeypatch.setenv("FORMUNEX_PLM_EMERGENCY_PHONE", "021-0001")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_RELEASE_REQUIRED_DOCS", "sds:zh-CN,label:zh-CN,tds:zh-CN,coa:*")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {name: accounts.create_user(s, username=name, password=PASSWORD, roles=roles).id
               for name, roles in USERS.items()}
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                           hazardous_catalog_no=2289)
        water = SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[])
        s.add_all([xyl, water])
        s.flush()
        locked = datetime(2026, 10, 1)
        for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
            s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                             h_code=h, source="manual", locked_at=locked, locked_by=ids["ehs"]))
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=water.id, hazard_class="not_classified",
                                         category="-", source="manual", locked_at=locked, locked_by=ids["ehs"]))
        s.add(FormulationVersion(id="fv-1", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30},
                                                           {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70}]}))
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in USERS:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = client.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100 环氧底漆"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1",
        "spec_snapshot": {"release_specs": [{"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60},
                                            {"metric": "density_g_cm3", "label": "密度", "unit": "g/cm³",
                                             "min": 1.3, "max": 1.4}]}}).json()["id"]
    yield {"client": client, "pid": pid, "rid": rid, "h": tokens, "ids": ids}
    get_settings.cache_clear()


PROPS = {"flash_point_c": 30, "initial_boiling_point_c": 138}


def _generate_all(e, *, as_user="rd", tds_density=1.36, coa_density=1.34):
    c, rid, pid, h = e["client"], e["rid"], e["pid"], e["h"][as_user]
    docs = {}
    docs["sds"] = c.post(f"/api/plm/revisions/{rid}/documents", headers=h, json={
        "doc_type": "sds", "properties": PROPS, "sds_properties": {"density_g_cm3": 1.35}}).json()
    docs["label"] = c.post(f"/api/plm/revisions/{rid}/documents", headers=h, json={
        "doc_type": "label", "properties": PROPS, "label": {"container_volume_l": 18}}).json()
    docs["tds"] = c.post(f"/api/plm/revisions/{rid}/documents", headers=h, json={
        "doc_type": "tds", "tds": {"product": {"description": "双组分环氧底漆"}, "application": {"method": "喷涂"},
                                   "properties": [{"key": "density_g_cm3", "label": "密度", "value": tds_density,
                                                   "unit": "g/cm³"}]}}).json()
    bid = c.post(f"/api/plm/products/{pid}/batches", json={"batch_no": f"B-{uuid.uuid4().hex[:6]}",
                                                          "revision_id": rid}).json()["id"]
    c.post(f"/api/plm/batches/{bid}/qc-results", json={"results": [{"metric": "solids", "value": 57.1},
                                                                  {"metric": "density_g_cm3", "value": coa_density}]})
    docs["coa"] = c.post(f"/api/plm/revisions/{rid}/documents", headers=h,
                         json={"doc_type": "coa", "coa": {"batch_id": bid}}).json()
    for k, d in docs.items():
        assert "id" in d, (k, d)
    return docs


def _approve_all(e, docs):
    c, h = e["client"], e["h"]
    for k, d in docs.items():
        who = "qa" if k == "coa" else "ehs"
        r = c.post(f"/api/plm/documents/{d['id']}/approve", headers=h[who], json={"password": PASSWORD})
        assert r.status_code == 200, (k, r.text)
        assert r.json()["status"] == "approved"


def _waive_open_items(e):
    c, rid, h = e["client"], e["rid"], e["h"]
    gate = c.get(f"/api/plm/revisions/{rid}/gate").json()
    for check in gate["checks"]:
        for item in check["items"]:
            if item["waivable"] and not item["waived"]:
                who = "ehs" if check["key"] == "data_gaps" else "lead"
                r = c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h[who], json={
                    "check": check["key"], "item": item["key"], "reason": "已评估，风险可接受", "password": PASSWORD})
                assert r.status_code == 201, r.text
    return c.get(f"/api/plm/revisions/{rid}/gate").json()


def _failed(gate):
    return {c["key"]: [i["message"] for i in c["items"] if not i["waived"]] for c in gate["checks"] if c["status"] == "fail"}


def _ready(e):
    docs = _generate_all(e)
    _approve_all(e, docs)
    c, rid, h = e["client"], e["rid"], e["h"]
    assert c.post(f"/api/plm/revisions/{rid}/candidate", headers=h["lead"]).json()["state"] == "candidate"
    gate = _waive_open_items(e)
    assert gate["passed"], _failed(gate)
    return docs


# --------------------------------------------------------------------------- accounts & tokens


def test_tokens_reject_tampering_expiry_and_other_algorithms(env):
    from app.services.identity import tokens

    tok, ttl = tokens.issue_access_token("u1", "s1", now=1_000_000)
    assert ttl == 15 * 60 and tokens.looks_like_access_token(tok)
    assert tokens.decode_access_token(tok, now=1_000_100)["sub"] == "u1"
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(tok, now=1_000_000 + ttl + 1)
    head, claims, sig = tok.split(".")
    forged_claims = tokens._b64(json.dumps({**json.loads(tokens._unb64(claims)), "sub": "admin"}).encode())
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(f"{head}.{forged_claims}.{sig}", now=1_000_100)
    none_head = tokens._b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(f"{none_head}.{claims}.", now=1_000_100)


def test_login_refresh_logout_and_lockout(env):
    c = env["client"]
    r = c.post("/api/identity/login", json={"username": "rd", "password": PASSWORD})
    access, refresh = r.json()["access_token"], r.cookies.get("fnx_refresh")
    assert refresh and r.json()["user"]["roles"] == ["rd_engineer"]
    me = c.get("/api/identity/me", headers={"Authorization": f"Bearer {access}"}).json()
    assert "plm.document.write" in me["permissions"] and "release.sign" not in me["permissions"]

    r2 = c.post("/api/identity/refresh")
    assert r2.status_code == 200 and r2.cookies.get("fnx_refresh") != refresh
    # The rotated-out refresh token is dead.
    c.cookies.clear()
    assert c.post("/api/identity/refresh", json={"refresh_token": refresh}).status_code == 401

    # Logging out revokes the session behind the access token immediately.
    new_access = r2.json()["access_token"]
    c.post("/api/identity/logout", json={"refresh_token": r2.json()["refresh_token"]})
    assert c.get("/api/identity/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 401

    for _ in range(5):
        assert c.post("/api/identity/login", json={"username": "rd", "password": "wrong"}).status_code == 401
    locked = c.post("/api/identity/login", json={"username": "rd", "password": PASSWORD})
    assert locked.status_code == 401 and "锁定" in locked.json()["detail"]
    assert c.post("/api/identity/login", json={"username": "ghost", "password": "x"}).json()["detail"] == "用户名或密码错误"


def test_user_admin_requires_permission(env):
    c, h = env["client"], env["h"]
    assert c.get("/api/identity/users", headers=h["rd"]).status_code == 403
    assert c.get("/api/identity/users").status_code == 401  # a person is required, not a service key
    r = c.post("/api/identity/users", headers=h["admin"], json={
        "username": "new.user", "password": "short", "roles": ["quality"]})
    assert r.status_code == 422
    r = c.post("/api/identity/users", headers=h["admin"], json={
        "username": "new.user", "password": PASSWORD, "roles": ["quality"]})
    assert r.status_code == 200 and r.json()["roles"] == ["quality"]
    r = c.put(f"/api/identity/users/{r.json()['id']}/roles", headers=h["admin"], json={"roles": ["nope"]})
    assert r.status_code == 422


def test_auth_middleware_accepts_access_tokens(env, monkeypatch):
    c, h = env["client"], env["h"]
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_API_TOKEN", "service-key-0123456789")
    get_settings.cache_clear()
    try:
        assert c.get("/api/identity/me", headers=h["rd"]).status_code == 200
        assert c.get("/api/plm/connectors", headers={"Authorization": "Bearer eyJ.garbage.sig"}).status_code == 401
        assert c.get("/api/plm/connectors", headers={"Authorization": "Bearer service-key-0123456789"}).status_code == 200
        # The service key reaches the API but cannot act as a person.
        r = c.get("/api/identity/me", headers={"Authorization": "Bearer service-key-0123456789"})
        assert r.status_code == 401
        assert c.post("/api/identity/login", json={"username": "rd", "password": PASSWORD}).status_code == 200
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------- documents


def test_document_approval_rules(env):
    c, h, rid = env["client"], env["h"], env["rid"]
    docs = _generate_all(env, as_user="ehs")
    sds = docs["sds"]
    assert sds["status"] == "draft"
    r = c.post(f"/api/plm/documents/{sds['id']}/approve", headers=h["rd"], json={"password": PASSWORD})
    assert r.status_code == 403  # rd_engineer cannot review
    r = c.post(f"/api/plm/documents/{sds['id']}/approve", headers=h["ehs"], json={"password": PASSWORD})
    assert r.status_code == 403 and "编制人" in r.json()["detail"]
    r = c.post(f"/api/plm/documents/{docs['coa']['id']}/approve", headers=h["ehs"], json={"password": PASSWORD})
    assert r.status_code == 403  # COA issue needs coa.sign (quality)
    # A newer version makes the older one unapprovable.
    newer = c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={
        "doc_type": "sds", "properties": PROPS, "sds_properties": {"density_g_cm3": 1.35}}).json()
    r = c.post(f"/api/plm/documents/{sds['id']}/approve", headers=h["qa"], json={"password": PASSWORD})
    assert r.status_code == 409 and "更新版本" in r.json()["detail"]
    r = c.post(f"/api/plm/documents/{newer['id']}/approve", headers=h["qa"], json={"password": "bad"})
    assert r.status_code == 401
    r = c.post(f"/api/plm/documents/{newer['id']}/approve", headers=h["qa"], json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# --------------------------------------------------------------------------- gate


def test_gate_lists_every_check_and_blocks_unapproved_documents(env):
    c, rid = env["client"], env["rid"]
    _generate_all(env)
    gate = c.get(f"/api/plm/revisions/{rid}/gate").json()
    assert [x["code"] for x in gate["checks"]] == [f"G{i}" for i in range(1, 11)]
    failed = _failed(gate)
    assert not gate["passed"]
    assert any("未审核" in m for m in failed["documents"])
    # Plan 1-1: the repo rule set is still draft, so the gate must also fail
    # classification_current with the "待 EHS 签核" message (no fixture here —
    # this test verifies the blocking behaviour itself).
    assert any("待 EHS 签核" in m for m in failed["classification_current"])
    for key in ("formula_total", "substance_data", "red_lines", "consistency",
                "sds_label", "required_fields"):
        assert key not in failed, (key, failed.get(key))
    assert next(x for x in gate["checks"] if x["key"] == "objectives")["status"] == "not_applicable"


def test_gate_detects_inconsistent_values_and_changed_data(env):
    c, rid = env["client"], env["rid"]
    _generate_all(env, tds_density=1.45)
    failed = _failed(c.get(f"/api/plm/revisions/{rid}/gate").json())
    assert any("density_g_cm3" in m for m in failed["consistency"])

    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion

    with default_session_factory()() as s:
        fv = s.get(FormulationVersion, "fv-1")
        fv.snapshot = {"ingredients": [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 40},
                                       {"name": "水", "cas_no": "7732-18-5", "weight_pct": 59}]}
        s.commit()
    failed = _failed(c.get(f"/api/plm/revisions/{rid}/gate").json())
    assert any("99.0000%" in m for m in failed["formula_total"])
    assert any("重新计算" in m for m in failed["classification_current"])


def test_gate_red_lines_unlocked_components_and_label_mismatch(env):
    c, rid = env["client"], env["rid"]
    from app.db.database import default_session_factory
    from app.db.models_plm import ProductRevisionRow, RegulatoryDocumentRow
    from app.db.models_regulatory import SubstanceClassificationRow

    docs = _generate_all(env)
    with default_session_factory()() as s:
        rev = s.get(ProductRevisionRow, rid)
        rev.spec_snapshot = {**rev.spec_snapshot, "red_lines": [{"cas": "1330-20-7", "max_pct": 25, "reason": "客户禁用"}]}
        for row in s.query(SubstanceClassificationRow).filter_by(hazard_class="skin_corrosion").all():
            row.locked_at = None
            row.source = "pubchem"
        label = s.get(RegulatoryDocumentRow, docs["label"]["id"])
        content = json.loads(json.dumps(label.content))
        content["meta"]["facts"]["pictograms"] = ["GHS02", "GHS06"]
        label.content = content
        s.commit()
    gate = c.get(f"/api/plm/revisions/{rid}/gate").json()
    failed = _failed(gate)
    assert any("客户禁用" in m for m in failed["red_lines"])
    assert any("置信度不足" in m for m in failed["substance_data"])
    assert any("pictograms" in m for m in failed["sds_label"])
    red = next(x for x in gate["checks"] if x["key"] == "red_lines")
    assert not red["waivable"] and not any(i["waivable"] for i in red["items"])


def test_gate_red_line_unresolved_list_ref_fails_closed(env):
    # P1-24：规约红线引用拼写错误时，门禁必须拦截（失败项 + 证据），不得静默放行。
    c, rid = env["client"], env["rid"]
    from app.db.database import default_session_factory
    from app.db.models_plm import ProductRevisionRow

    _generate_all(env)
    with default_session_factory()() as s:
        rev = s.get(ProductRevisionRow, rid)
        rev.spec_snapshot = {**rev.spec_snapshot, "red_lines": [
            {"list_id": "new_pollutants_2023", "list_entry_id": "NP-99", "max_pct": 0}]}
        s.commit()
    gate = c.get(f"/api/plm/revisions/{rid}/gate").json()
    failed = _failed(gate)
    assert any("引用未解析" in m for m in failed["red_lines"])
    red = next(x for x in gate["checks"] if x["key"] == "red_lines")
    assert not any(i["waivable"] for i in red["items"])
    assert "new_pollutants_2023:NP-99" in (red.get("evidence") or {}).get("red_lines_unresolved", [])


def test_waivers_only_for_waivable_items(env):
    c, rid, h = env["client"], env["rid"], env["h"]
    from app.db.database import default_session_factory
    from app.db.models_plm import ProductRevisionRow

    with default_session_factory()() as s:
        rev = s.get(ProductRevisionRow, rid)
        rev.spec_snapshot = {**rev.spec_snapshot, "acceptance": [{"metric": "salt_spray_hours", "label": "耐盐雾", "min": 500}]}
        s.commit()
    gate = c.get(f"/api/plm/revisions/{rid}/gate").json()
    obj = next(x for x in gate["checks"] if x["key"] == "objectives")
    assert obj["status"] == "fail" and obj["items"][0]["key"] == "salt_spray_hours"
    body = {"check": "objectives", "item": "salt_spray_hours", "reason": "客户同意以 480 h 交付", "password": PASSWORD}
    assert c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["qa"], json=body).status_code == 403
    assert c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["lead"], json={**body, "item": "nope"}).status_code == 409
    r = c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["lead"], json=body)
    assert r.status_code == 201
    obj = next(x for x in c.get(f"/api/plm/revisions/{rid}/gate").json()["checks"] if x["key"] == "objectives")
    assert obj["status"] == "waived" and obj["items"][0]["waiver"]["reason"] == "客户同意以 480 h 交付"
    r = c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["lead"], json={**body, "check": "red_lines"})
    assert r.status_code == 422  # not a waivable check


# --------------------------------------------------------------------------- sign-off


def test_full_release_flow(env, signed_off_ruleset):
    c, rid, pid, h = env["client"], env["rid"], env["pid"], env["h"]
    _ready(env)
    assert c.post(f"/api/plm/revisions/{rid}/submit", headers=h["rd"]).status_code == 403
    r = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"])
    assert r.status_code == 201, r.text
    pkg = r.json()
    assert pkg["status"] == "under_review" and [s["role"] for s in pkg["flow"]["steps"]] == [
        "project_lead", "ehs_regulatory", "quality", "release_manager"]
    assert len(pkg["package_hash"]) == 64 and len(pkg["document_ids"]) == 4
    # Documents are frozen while under review.
    r = c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={"doc_type": "sds", "properties": PROPS})
    assert r.status_code == 422 and "签批" in r.json()["detail"]

    sign = lambda who, **kw: c.post(f"/api/plm/packages/{pkg['id']}/sign", headers=h[who],  # noqa: E731
                                    json={"decision": "approve", "password": PASSWORD, **kw})
    assert sign("ehs").status_code == 403  # step 1 belongs to the project lead
    assert sign("lead").status_code == 200
    assert sign("lead").status_code == 403  # not the EHS role (and already signed)
    assert sign("ehs", password="wrong").status_code == 401
    assert sign("ehs").status_code == 200
    assert sign("qa").status_code == 200
    final = sign("rm")
    assert final.status_code == 200, final.text
    pkg = final.json()
    assert pkg["status"] == "released" and pkg["flow"]["status"] == "approved"
    assert [s["meaning"] for s in pkg["signatures"]] == ["approve", "approve", "approve", "release"]
    assert pkg["signatures_valid"]

    product = c.get(f"/api/plm/products/{pid}").json()
    assert product["lifecycle_state"] == "released"
    assert product["revisions"][0]["state"] == "released" and product["revisions"][0]["frozen_at"]
    assert {d["status"] for d in c.get(f"/api/plm/revisions/{rid}/documents").json()
            if d["id"] in pkg["document_ids"]} == {"released"}

    z = c.get(f"/api/plm/packages/{pkg['id']}/delivery")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = zf.namelist()
    assert manifest["package_hash"] == pkg["package_hash"] and len(manifest["signatures"]) == 4
    assert any(n.startswith("sds/") and n.endswith(".pdf") for n in names)
    from app.services.plm.release.package import package_hash

    assert package_hash(manifest["manifest"]) == pkg["package_hash"]

    audit = c.get("/api/plm/audit", headers=h["rm"]).json()
    assert audit["chain_ok"] and {"release.publish", "esign.release", "release.submit"} <= {
        r["action"] for r in audit["records"]}
    assert c.get("/api/plm/audit", headers=h["rd"]).status_code == 403

    # Withdraw.
    assert c.post(f"/api/plm/packages/{pkg['id']}/withdraw", headers=h["lead"],
                  json={"reason": "x", "password": PASSWORD}).status_code == 403
    r = c.post(f"/api/plm/packages/{pkg['id']}/withdraw", headers=h["rm"],
               json={"reason": "标签印刷错误", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert c.get(f"/api/plm/products/{pid}").json()["lifecycle_state"] == "withdrawn"


def test_rejection_returns_to_candidate_and_tampering_stops_signing(env, signed_off_ruleset):
    c, rid, h = env["client"], env["rid"], env["h"]
    _ready(env)
    pkg = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).json()
    url = f"/api/plm/packages/{pkg['id']}/sign"
    assert c.post(url, headers=h["lead"], json={"decision": "approve", "password": PASSWORD}).status_code == 200
    assert c.post(url, headers=h["ehs"], json={"decision": "reject", "password": PASSWORD}).status_code == 409
    r = c.post(url, headers=h["ehs"], json={"decision": "reject", "password": PASSWORD, "comment": "第 8 节防护不足"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert c.get(f"/api/plm/products/{env['pid']}").json()["revisions"][0]["state"] == "candidate"

    # Resubmit, then change a stored file: the next signature is refused.
    pkg2 = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).json()
    from app.db.database import default_session_factory
    from app.db.models_plm import RegulatoryDocumentRow
    from app.services.plm import storage

    with default_session_factory()() as s:
        doc = s.get(RegulatoryDocumentRow, pkg2["document_ids"][0])
        storage.put(doc.rendered_pdf_key, b"%PDF-1.4 tampered")
    r = c.post(f"/api/plm/packages/{pkg2['id']}/sign", headers=h["lead"], json={"decision": "approve", "password": PASSWORD})
    assert r.status_code == 409 and "不一致" in r.json()["detail"]


def test_submit_refused_when_gate_fails(env):
    c, rid, h = env["client"], env["rid"], env["h"]
    assert c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).status_code == 409  # not a candidate
    c.post(f"/api/plm/revisions/{rid}/candidate", headers=h["lead"])
    r = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"])
    assert r.status_code == 409 and r.json()["detail"]["gate"]["passed"] is False


def test_new_release_supersedes_the_previous_revision(env, signed_off_ruleset):
    c, rid, pid, h = env["client"], env["rid"], env["pid"], env["h"]
    _ready(env)
    pkg = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).json()
    for who in ("lead", "ehs", "qa", "rm"):
        assert c.post(f"/api/plm/packages/{pkg['id']}/sign", headers=h[who],
                      json={"decision": "approve", "password": PASSWORD}).status_code == 200
    rid2 = c.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1", "spec_snapshot": {"release_specs": [
            {"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60},
            {"metric": "density_g_cm3", "label": "密度", "unit": "g/cm³", "min": 1.3, "max": 1.4}]}}).json()["id"]
    e2 = {**env, "rid": rid2}
    _ready(e2)
    pkg2 = c.post(f"/api/plm/revisions/{rid2}/submit", headers=h["lead"]).json()
    for who in ("lead", "ehs", "qa", "rm"):
        assert c.post(f"/api/plm/packages/{pkg2['id']}/sign", headers=h[who],
                      json={"decision": "approve", "password": PASSWORD}).status_code == 200
    states = {r["rev"]: r["state"] for r in c.get(f"/api/plm/products/{pid}").json()["revisions"]}
    assert states == {"A": "superseded", "B": "released"}
    assert {d["status"] for d in c.get(f"/api/plm/revisions/{rid}/documents").json()
            if d["id"] in pkg["document_ids"]} == {"superseded"}


# --------------------------------------------------------------------------- connectors


def test_webhook_connector_signs_and_retries():
    from app.services.connectors.base import DeliveryPackage
    from app.services.connectors.webhook import WebhookConnector, signature

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503 if len(seen) < 3 else 200)

    conn = WebhookConnector("https://example.com/hook", "s3cret",
                            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
                            sleep=lambda _s: None)
    pkg = DeliveryPackage("p1", "FX-100", "A", "h" * 64, {"documents": []})
    result = conn.publish(pkg)
    assert result.ok and result.attempts == 3
    req = seen[-1]
    assert req.headers["X-FormuNex-Event"] == "release.published"
    assert req.headers["X-FormuNex-Signature"] == signature("s3cret", req.content)
    assert json.loads(req.content)["package_hash"] == "h" * 64

    seen.clear()
    rejected = WebhookConnector("https://example.com/hook", client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(400))[1])), sleep=lambda _s: None)
    r = rejected.withdraw(pkg, "recall")
    assert not r.ok and r.attempts == 1 and r.response_status == 400  # 4xx is not retried
    with pytest.raises(ValueError):
        WebhookConnector("ftp://example.com/x")


def test_connector_failures_are_recorded_not_raised(env, signed_off_ruleset):
    c, rid, h = env["client"], env["rid"], env["h"]
    _ready(env)
    pkg = c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]).json()
    for who in ("lead", "ehs", "qa", "rm"):
        c.post(f"/api/plm/packages/{pkg['id']}/sign", headers=h[who], json={"decision": "approve", "password": PASSWORD})

    class Boom:
        name = "boom"

        def publish(self, package):
            raise RuntimeError("down")

    from app.db.database import default_session_factory
    from app.services.plm.release import signoff

    with default_session_factory()() as s:
        results = signoff.run_connectors(s, pkg["id"], connectors=[Boom()])
        s.commit()
    assert results[0]["ok"] is False and "down" in results[0]["detail"]
    assert c.get(f"/api/plm/packages/{pkg['id']}").json()["publish_results"][0]["connector"] == "boom"
    listing = c.get("/api/plm/connectors").json()
    assert {r["name"] for r in listing["reserved"]} == {"erp", "document_system", "website"}


def test_required_docs_setting_parses():
    from app.services.plm.release.gate import required_docs

    get_settings.cache_clear()
    assert ("coa", "*") in required_docs() and ("sds", "en-US") in required_docs()


def test_production_without_signing_key_refuses_sign_in(env, monkeypatch):
    from app.services.identity import tokens
    from app.startup_checks import run_startup_checks

    monkeypatch.setenv("FORMUNEX_ENVIRONMENT", "production")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "too-short")
    get_settings.cache_clear()
    try:
        assert any("FORMUNEX_AUTH_JWT_SECRET" in w for w in run_startup_checks(get_settings()))
        with pytest.raises(tokens.SigningKeyMissing):
            tokens.issue_access_token("u", "s")
        r = env["client"].post("/api/identity/login", json={"username": "rd", "password": PASSWORD})
        assert r.status_code == 503
    finally:
        get_settings.cache_clear()
