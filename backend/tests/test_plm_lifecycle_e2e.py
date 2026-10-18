"""§7 end-to-end: the whole R&D → PLM chain through the HTTP API, offline (no LLM, sqlite, Celery eager).

literature ingest / KB search → project spec contract → constrained-mixture DOE (persisted, compliance-screened)
→ lab tasks → entry, submit, review by another account → one optimisation round on the reviewed data → 设为定型
→ GB 30000 classification → SDS / TDS / label / COA (batch QC) → release gate → four-step sign-off with
re-entered passwords → released package, manifest hashes verified, lineage traced back to the formulation version,
DOE plan, reviewed measurements and the classification rule set.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")
pytest.importorskip("svglib")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

PW = "Fx-Lifecycle-2026"
USERS = {"lead": ["project_lead"], "rd": ["rd_engineer"], "tech": ["lab_technician"], "qa": ["quality"],
         "ehs": ["ehs_regulatory"], "rm": ["release_manager"], "auditor": ["sys_admin"]}

# name, CAS, DOE range (wt%), locked classifications (hazard_class, category, H code)
COMPONENTS = [
    ("环氧树脂 E51", "25068-38-6", 30, 50, [("skin_corrosion", "2", "H315")]),
    ("二甲苯", "1330-20-7", 10, 25, [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]),
    ("磷酸锌", "7779-90-0", 5, 15, [("not_classified", "-", None)]),
    ("滑石粉", "14807-96-6", 20, 50, [("not_classified", "-", None)]),
]
EPOXY = COMPONENTS[0][0]
SPEC_CONTRACT = {
    "red_lines": [{"cas": "1330-20-7", "name": "二甲苯", "max_pct": 30, "reason": "VOC 控制"}],
    "forbidden_pictograms": ["GHS06"],
    "acceptance": [{"metric": "salt_spray_hours", "label": "耐盐雾", "min": 600}],
    "release_specs": [{"metric": "density_g_cm3", "label": "密度", "unit": "g/cm³", "method": "GB/T 6750",
                       "min": 1.30, "max": 1.45}],
}
LITERATURE = """# 环氧防腐底漆专利

## 实施例 1

环氧树脂 E51 四十五质量份，磷酸锌防锈颜料十份，滑石粉三十份，二甲苯十五份，混合研磨后喷涂。
盐雾试验通过七百二十小时无起泡无锈蚀。

## 实施例 2

环氧树脂降至三十份时盐雾试验四百八十小时出现锈蚀。
"""


def _salt_spray(run: dict) -> float:
    """Synthetic lab response: more epoxy, better corrosion resistance."""
    return round(300 + 10 * float(run[EPOXY]))


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'lifecycle.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "true")
    # NOTE: KB v2 必须保持开启（默认 True）。KB_V2_ENABLED=false 时
    # index_source 直接返回 0、不写任何 chunk，且检索端同样返回 []，
    # _knowledge 步的 KB 搜索永远为空。此前 fixture 误关了它，导致
    # test_plm_lifecycle_end_to_end 从提交起就红（CI 35 起）。
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "方枢示例化工有限公司")
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_ADDRESS", "上海市示例路 1 号")
    monkeypatch.setenv("FORMUNEX_PLM_EMERGENCY_PHONE", "021-0001")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "lifecycle-secret-" + "q" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_RELEASE_REQUIRED_DOCS", "sds:zh-CN,label:zh-CN,tds:zh-CN,coa:*")
    get_settings.cache_clear()
    # Process-wide stores bind to the database of their first use; rebind them to this test's database.
    import app.db.campaign_store
    import app.db.chunk_store
    import app.db.entity_store
    import app.db.measurement_store
    import app.db.project_store
    import app.db.source_store
    import app.db.store
    import app.db.toc_tree_store
    import app.services.formulation_history

    for mod in (app.db.campaign_store, app.db.chunk_store, app.db.entity_store, app.db.measurement_store,
                app.db.project_store, app.db.source_store, app.db.store, app.db.toc_tree_store,
                app.services.formulation_history):
        monkeypatch.setattr(mod, "_store", None)

    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {n: accounts.create_user(s, username=n, password=PW, roles=r).id for n, r in USERS.items()}
        locked = datetime(2026, 10, 1)
        for name, cas, _lo, _hi, classes in COMPONENTS:
            sub = SubstanceRow(id=str(uuid.uuid4()), cas_no=cas, name_zh=name, aliases=[])
            s.add(sub)
            s.flush()
            for hc, cat, h in classes:
                s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=sub.id, hazard_class=hc,
                                                 category=cat, h_code=h, source="manual", locked_at=locked,
                                                 locked_by=ids["ehs"]))
        s.commit()
    from app.main import app

    c = TestClient(app)
    h = {}
    for n in USERS:
        r = c.post("/api/identity/login", json={"username": n, "password": PW})
        assert r.status_code == 200, r.text
        h[n] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"c": c, "h": h, "ids": ids}
    get_settings.cache_clear()


def _ok(r, status=200):
    assert r.status_code == status, (r.request.method, str(r.request.url), r.status_code, r.text[:2000])
    return r.json()


# --------------------------------------------------------------------------- steps


def _knowledge(c) -> None:
    out = _ok(c.post("/api/ingest/text", json={"text": LITERATURE, "title": "环氧防腐底漆专利"}))
    assert out["extraction_status"] != "skipped"
    hits = _ok(c.get("/api/kb/search", params={"q": "盐雾 环氧树脂", "k": 3}))["results"]
    assert hits and any("盐雾" in (x.get("snippet") or "") for x in hits)


def _project(c) -> tuple[str, dict]:
    requirement = {"domain": "anticorrosion_coating", "salt_spray_hours": 600,
                   "objectives": [{"metric": "salt_spray_hours", "direction": "maximize", "weight": 1.0}],
                   "levers": [{"name": n, "low": lo, "high": hi, "unit": "wt%"} for n, _c, lo, hi, _x in COMPONENTS]}
    pid = _ok(c.post("/api/projects", json={"title": "FX-300 环氧防腐底漆", "requirement": requirement}))["id"]
    _ok(c.put(f"/api/projects/{pid}", json={"workspace": {"requirement": {**requirement, "project_id": pid},
                                                          "spec_contract": SPEC_CONTRACT}}))
    project = _ok(c.get(f"/api/projects/{pid}"))
    assert project["workspace"]["spec_contract"]["acceptance"][0]["min"] == 600
    return pid, project


def _doe(c, project_id: str, spec: dict) -> tuple[str, list[dict]]:
    body = {"components": [{"name": n, "low": lo, "high": hi, "cas": cas} for n, cas, lo, hi, _x in COMPONENTS],
            "mixture_order": "linear", "n_runs": 6, "persist": True, "project_id": project_id,
            "screen": {"red_lines": {"substances": spec["red_lines"],
                                     "forbidden_pictograms": spec["forbidden_pictograms"]},
                       "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}, "drop_flagged": True}}
    out = _ok(c.post("/api/doe/designs/mixture", json=body))
    plan = out["plan"]
    assert plan["plan_id"] and len(plan["runs"]) == len(out["runs"]) >= 4
    for run in out["runs"]:
        assert abs(sum(float(run[n]) for n, *_ in COMPONENTS) - 100) < 1e-6
    stored = _ok(c.get(f"/api/doe/plans/{plan['plan_id']}"))
    assert len(stored["runs"]) == len(plan["runs"])
    return plan["plan_id"], plan["runs"]


def _lab(c, h, plan_id: str, project_id: str, runs: list[dict]) -> list[dict]:
    assert _ok(c.post("/api/lab/methods/seed", headers=h["qa"]))["added"] >= 15
    tasks = _ok(c.post("/api/lab/tasks/dispatch", headers=h["lead"], json={
        "doe_plan_id": plan_id, "project_id": project_id, "domain": "anticorrosion_coating",
        "metrics": ["salt_spray_hours", "density_g_cm3", "flash_point_c"]}), 201)["created"]
    assert len(tasks) == len(runs)
    natural = {r["run_id"]: r["natural"] for r in runs}
    for t in tasks:
        values = [{"metric": "salt_spray_hours", "value": _salt_spray(natural[t["run_index"]]), "method_code": "GB/T 10125"},
                  {"metric": "density_g_cm3", "value": 1.362, "method_code": "GB/T 6750"},
                  {"metric": "flash_point_c", "value": 30, "method_code": "GB/T 261"}]
        recorded = _ok(c.post(f"/api/lab/tasks/{t['id']}/measurements", headers=h["tech"],
                              json={"measurements": values}), 201)["measurements"]
        assert all(m["review_status"] == "unreviewed" for m in recorded)
        assert _ok(c.post(f"/api/lab/tasks/{t['id']}/submit", headers=h["tech"]))["status"] == "pending_review"
        # The operator cannot review their own values; quality reviews them.
        assert c.post(f"/api/lab/measurements/{recorded[0]['id']}/review", headers=h["tech"],
                      json={"decision": "accept"}).status_code == 403
        for m in recorded:
            assert _ok(c.post(f"/api/lab/measurements/{m['id']}/review", headers=h["qa"],
                              json={"decision": "accept"}))["review_status"] == "reviewed"
    done = _ok(c.get("/api/lab/tasks", params={"doe_plan_id": plan_id}))
    assert {t["status"] for t in done} == {"done"}
    assert all(t["measured"]["salt_spray_hours"] == _salt_spray(natural[t["run_index"]]) for t in done)
    return done


def _optimise(c, project: dict, tasks: list[dict]) -> dict:
    """One optimisation round on the reviewed lab data (the offline active-learning path; BayBE when installed)."""
    req = project["workspace"]["requirement"]
    records = [{"domain": "anticorrosion_coating", "project_id": project["id"], "factors": t["factors"],
                "measured": t["measured"], "label": t["task_no"]} for t in tasks]
    out = _ok(c.post("/api/doe/active", json={**req, "existing_records": records, "n_suggest": 2, "seed": 7}))
    assert out["plan"]["runs"] and out["plan_id"]
    return out


def _winning(c, h, project_id: str, spec: dict, tasks: list[dict]) -> tuple[str, str, dict]:
    best = max(tasks, key=lambda t: t["measured"]["salt_spray_hours"])
    assert best["measured"]["salt_spray_hours"] >= spec["acceptance"][0]["min"]
    by_name = {n: cas for n, cas, *_ in COMPONENTS}
    ingredients = [{"name": n, "cas_no": by_name[n], "role": "resin" if n == EPOXY else "other",
                    "weight_pct": float(v)} for n, v in best["factors"].items()]
    fv = _ok(c.post("/api/formulations/versions", json={
        "formulation": {"name": "FX-300", "domain": "anticorrosion_coating", "ingredients": ingredients,
                        "source": "manual"},
        "change_summary": f"DOE 定型点 {best['task_no']}", "project_id": project_id}))
    product_id = _ok(c.post("/api/plm/products", json={"code": "FX-300", "name_zh": "FX-300 环氧防腐底漆",
                                                       "project_id": project_id}), 201)["id"]
    body = {"product_id": product_id, "formulation_version_id": fv["id"], "experiment_id": best["experiment_id"],
            "spec_snapshot": {k: spec[k] for k in ("release_specs", "acceptance", "red_lines")}, "note": "第 1 轮最优"}
    assert c.post("/api/cockpit/winning", json=body, headers=h["rd"]).status_code == 403
    win = _ok(c.post("/api/cockpit/winning", json=body, headers=h["lead"]), 201)
    assert win["state"] == "candidate" and win["warnings"] == []
    return product_id, win["revision_id"], {"fv": fv["id"], "best": best}


def _classify(c, fv_id: str) -> dict:
    out = _ok(c.post(f"/api/plm/ghs/classify/formulation-versions/{fv_id}", json={
        "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}}))
    assert out["classification_id"]
    return out


def _documents(c, h, product_id: str, rid: str) -> dict[str, dict]:
    cand = _ok(c.get(f"/api/plm/revisions/{rid}/property-candidates"))["sds_properties"]
    assert cand["density_g_cm3"]["source"] == "measured" and cand["density_g_cm3"]["value"] == 1.36
    props = {"flash_point_c": 30, "initial_boiling_point_c": 138}
    docs = {}
    docs["sds"] = _ok(c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={
        "doc_type": "sds", "properties": props, "sds_properties": cand}), 201)
    docs["label"] = _ok(c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={
        "doc_type": "label", "properties": props, "label": {"container_volume_l": 18}}), 201)
    docs["tds"] = _ok(c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"], json={
        "doc_type": "tds", "tds": {"product": {"description": "双组分环氧防腐底漆"}, "application": {"method": "喷涂"},
                                   "properties": [{"key": "density_g_cm3", "label": "密度", "value": 1.36,
                                                   "unit": "g/cm³"}]}}), 201)
    bid = _ok(c.post(f"/api/plm/products/{product_id}/batches", json={"batch_no": "B261017", "revision_id": rid}),
              201)["id"]
    _ok(c.post(f"/api/plm/batches/{bid}/qc-results", json={"results": [
        {"metric": "density_g_cm3", "value": 1.35, "unit": "g/cm³", "test_method": "GB/T 6750"}]}), 201)
    docs["coa"] = _ok(c.post(f"/api/plm/revisions/{rid}/documents", headers=h["rd"],
                             json={"doc_type": "coa", "coa": {"batch_id": bid}}), 201)
    for kind, d in docs.items():
        assert d["has_pdf"], kind
        reviewer = "qa" if kind == "coa" else "ehs"
        assert _ok(c.post(f"/api/plm/documents/{d['id']}/approve", headers=h[reviewer],
                          json={"password": PW}))["status"] == "approved"
    return docs


def _gate(c, h, rid: str) -> dict:
    gate = _ok(c.get(f"/api/plm/revisions/{rid}/gate"))
    waived = []
    for check in gate["checks"]:
        for item in check["items"]:
            if item["waivable"] and not item["waived"]:
                who = "ehs" if check["key"] == "data_gaps" else "lead"
                _ok(c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h[who], json={
                    "check": check["key"], "item": item["key"], "reason": "已评估，风险可接受", "password": PW}), 201)
                waived.append((check["key"], item["key"]))
    gate = _ok(c.get(f"/api/plm/revisions/{rid}/gate"))
    failed = {x["key"]: [i["message"] for i in x["items"] if not i["waived"]] for x in gate["checks"]
              if x["status"] == "fail"}
    assert gate["passed"], failed
    by_key = {x["key"]: x for x in gate["checks"]}
    # Only documented waivers: the measured objective and the red lines pass on their own.
    assert by_key["objectives"]["status"] == "pass" and by_key["red_lines"]["status"] == "pass"
    assert {k for k, _i in waived} <= {"data_gaps"}, waived
    return gate


def _sign_off(c, h, rid: str) -> dict:
    pkg = _ok(c.post(f"/api/plm/revisions/{rid}/submit", headers=h["lead"]), 201)
    steps = [s["role"] for s in pkg["flow"]["steps"]]
    assert steps == ["project_lead", "ehs_regulatory", "quality", "release_manager"]
    url = f"/api/plm/packages/{pkg['id']}/sign"
    signers = ["lead", "ehs", "qa", "rm"]
    for i, who in enumerate(signers):
        assert c.post(url, headers=h[who], json={"decision": "approve", "password": "wrong-password"}).status_code == 401
        if i:
            assert c.post(url, headers=h[signers[i - 1]], json={"decision": "approve", "password": PW}).status_code == 403
        pkg = _ok(c.post(url, headers=h[who], json={"decision": "approve", "password": PW}))
    assert pkg["status"] == "released" and pkg["signatures_valid"]
    assert len({s["user_id"] for s in pkg["signatures"]}) == 4
    return pkg


def _verify_delivery(c, pkg: dict) -> dict:
    from app.services.plm.release.package import package_hash

    z = c.get(f"/api/plm/packages/{pkg['id']}/delivery")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        files = {n: zf.read(n) for n in zf.namelist() if n != "manifest.json"}
    assert manifest["package_hash"] == pkg["package_hash"] == package_hash(manifest["manifest"])
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    assert set(listed) == set(files)
    for path, data in files.items():
        assert hashlib.sha256(data).hexdigest() == listed[path], path
    file_hashes = set(listed.values())
    for d in manifest["manifest"]["documents"]:
        assert d["pdf_sha256"] in file_hashes, d
    assert {d["doc_type"] for d in manifest["manifest"]["documents"]} == {"sds", "tds", "label", "coa"}
    return manifest


# --------------------------------------------------------------------------- the chain


def test_plm_lifecycle_end_to_end(stack, signed_off_ruleset):
    c, h = stack["c"], stack["h"]

    _knowledge(c)
    project_id, project = _project(c)
    spec = project["workspace"]["spec_contract"]
    plan_id, runs = _doe(c, project_id, spec)
    tasks = _lab(c, h, plan_id, project_id, runs)
    _optimise(c, project, tasks)
    product_id, rid, win = _winning(c, h, project_id, spec, tasks)
    classification = _classify(c, win["fv"])
    docs = _documents(c, h, product_id, rid)
    _gate(c, h, rid)
    pkg = _sign_off(c, h, rid)
    manifest = _verify_delivery(c, pkg)

    # ---- lineage: package → revision → formulation version / winning experiment → DOE plan → reviewed data
    assert manifest["manifest"]["revision"] == {"id": rid, "rev": "A", "formulation_version_id": win["fv"]}
    rev = _ok(c.get(f"/api/plm/revisions/{rid}"))
    assert rev["formulation_version_id"] == win["fv"] and rev["state"] == "released"
    assert rev["winning_experiment_id"] == win["best"]["experiment_id"]
    fv = _ok(c.get(f"/api/formulations/versions/detail/{win['fv']}"))
    assert {i["name"]: i["weight_pct"] for i in fv["snapshot"]["ingredients"]} == win["best"]["factors"]
    task = next(t for t in _ok(c.get("/api/lab/tasks", params={"doe_plan_id": plan_id}))
                if t["experiment_id"] == rev["winning_experiment_id"])
    assert task["doe_plan_id"] == plan_id and task["project_id"] == project_id
    detail = _ok(c.get(f"/api/lab/tasks/{task['id']}"))
    assert {m["review_status"] for m in detail["measurements"]} == {"reviewed"}
    assert {m["operator"] for m in detail["measurements"]} == {"tech"}
    assert rev["spec_snapshot"]["winning"]["measured"]["salt_spray_hours"] == task["measured"]["salt_spray_hours"]

    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow, LineageEdgeRow

    sds_entry = next(d for d in manifest["manifest"]["documents"] if d["doc_type"] == "sds")
    sds = _ok(c.get(f"/api/plm/documents/{sds_entry['id']}"))
    assert sds["id"] == docs["sds"]["id"]
    ruleset = _ok(c.get("/api/plm/ghs/ruleset"))["version"]
    with default_session_factory()() as s:
        cls_row = s.get(ClassificationResultRow, sds["content"]["meta"]["classification_id"])
        assert cls_row.formulation_version_id == win["fv"] and cls_row.rule_set_version == ruleset
        assert s.get(ClassificationResultRow, classification["classification_id"]).inputs_hash == cls_row.inputs_hash
        edges = {(e.from_type, e.from_id, e.relation) for e in s.query(LineageEdgeRow).filter_by(to_id=rid)}
    # Phase 2D (2-22): create_revision also links product -> revision (has_revision),
    # so the revision's incoming edges are the winning pair plus the product edge.
    assert edges == {("experiment", str(win["best"]["experiment_id"]), "selected_as_winning"),
                     ("formulation_version", win["fv"], "frozen_as"),
                     ("product", product_id, "has_revision")}

    # ---- the whole chain is in the hash-chained audit log, intact
    audit = _ok(c.get("/api/plm/audit", headers=h["rm"], params={"limit": 1000}))
    assert audit["chain_ok"], audit["first_bad_seq"]
    actions = {r["action"] for r in audit["records"]}
    assert {"revision.winning", "release.submit", "esign.release", "release.publish"} <= actions
