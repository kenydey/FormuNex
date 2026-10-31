"""M7: lab execution — methods, instruments, tasks from DOE plans, entry checks, review, amendments,
value picks, instrument files, SDS / conformal feeds, eLabFTW sync and conflicts, Datalab migration."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.lab import parsers
from app.services.lab.methods import LabError

PW = "Fx-Lab-2026-ok"
USERS = {"tech": ["lab_technician"], "tech2": ["lab_technician"], "qa": ["quality"], "lead": ["project_lead"],
         "rd": ["rd_engineer"]}


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'lab.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "lab-secret-" + "z" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {n: accounts.create_user(s, username=n, password=PW, roles=r).id for n, r in USERS.items()}
        s.commit()
    from app.main import app

    c = TestClient(app)
    h = {n: {"Authorization": "Bearer " + c.post("/api/identity/login", json={"username": n, "password": PW}).json()[
        "access_token"]} for n in USERS}
    assert c.post("/api/lab/methods/seed", headers=h["qa"]).json()["added"] >= 15
    yield {"c": c, "h": h, "ids": ids}
    get_settings.cache_clear()


def _plan(c, factors=2):
    # P1-2 tightening: persisted plans require a project (dispatch falls
    # back to the plan's project when the dispatch body omits one).
    body = {"factors": [{"name": f"F{i}", "levels": [10 * i, 20 * i]} for i in range(1, factors + 1)],
            "persist": True, "project_id": "proj-test"}
    r = c.post("/api/doe/designs/orthogonal", json=body)
    assert r.status_code == 200, r.text
    return r.json()["plan"]["plan_id"]


def _dispatch(lab, plan_id, metrics=("density_g_cm3", "salt_spray_hours"), predictions=None):
    r = lab["c"].post("/api/lab/tasks/dispatch", headers=lab["h"]["lead"], json={
        "doe_plan_id": plan_id, "metrics": list(metrics), "domain": "anticorrosion_coating",
        "predictions": predictions or {}})
    assert r.status_code == 201, r.text
    return r.json()["created"]


def _record(lab, who, task_id, *entries):
    return lab["c"].post(f"/api/lab/tasks/{task_id}/measurements", headers=lab["h"][who],
                         json={"measurements": list(entries)})


DENSITY = {"metric": "density_g_cm3", "value": 1.347, "method_code": "GB/T 6750"}
SALT = {"metric": "salt_spray_hours", "value": 480, "method_code": "GB/T 10125"}


# --------------------------------------------------------------------------- methods / tasks


def test_method_library_and_permissions(lab):
    c, h = lab["c"], lab["h"]
    codes = {m["code"] for m in c.get("/api/lab/methods").json()}
    assert {"GB/T 10125", "GB/T 9286", "GB/T 6750", "GB/T 261"} <= codes
    assert c.post("/api/lab/methods/seed", headers=h["qa"]).json()["added"] == 0  # idempotent
    body = {"code": "Q/FX 001", "name_zh": "企业标准 耐酸性", "metric": "acid_resistance_h", "unit": "h",
            "valid_min": 0, "valid_max": 2000}
    assert c.post("/api/lab/methods", headers=h["tech"], json=body).status_code == 403
    assert c.post("/api/lab/methods", headers=h["qa"], json=body).status_code == 201
    assert c.post("/api/lab/methods", headers=h["qa"], json=body).status_code == 422


def test_dispatch_is_one_task_per_run_and_idempotent(lab):
    c = lab["c"]
    plan_id = _plan(c)
    tasks = _dispatch(lab, plan_id)
    assert len(tasks) == 4 and {t["run_index"] for t in tasks} == {1, 2, 3, 4}
    t = tasks[0]
    assert re.fullmatch(r"LT-\d{8}-\d{4}", t["task_no"]) and t["sample_no"] == t["task_no"] + "-S1"
    assert t["status"] == "open" and t["experiment_id"] and set(t["factors"]) == {"F1", "F2"}
    again = c.post("/api/lab/tasks/dispatch", headers=lab["h"]["lead"], json={
        "doe_plan_id": plan_id, "metrics": ["density_g_cm3"], "domain": "anticorrosion_coating"}).json()
    assert again["created"] == [] and len(again["skipped"]) == 4
    assert c.post("/api/lab/tasks/dispatch", headers=lab["h"]["tech"], json={
        "doe_plan_id": plan_id, "metrics": ["x"], "domain": "anticorrosion_coating"}).status_code == 403
    scanned = c.get(f"/api/lab/samples/{t['sample_no']}").json()
    assert scanned["task"]["id"] == t["id"]


def test_3_17_dispatch_task_factors_match_doe_matrix(lab):
    """3-17：任务单因子取值逐行 = DOE 矩阵行 natural 值（不仅键名一致），待测指标与下发一致。"""
    c = lab["c"]
    plan_id = _plan(c)
    plan = c.get(f"/api/doe/plans/{plan_id}").json()
    by_run = {r["run_id"]: r["natural"] for r in plan["runs"]}
    assert len(by_run) == 4
    tasks = _dispatch(lab, plan_id)
    assert {t["run_index"] for t in tasks} == set(by_run)
    for t in tasks:
        assert t["factors"] == by_run[t["run_index"]], (t["run_index"], t["factors"], by_run[t["run_index"]])
        assert {m["metric"] for m in t["requested_metrics"]} == {"density_g_cm3", "salt_spray_hours"}


# --------------------------------------------------------------------------- entry checks


def test_entry_validation_rounding_and_ownership(lab):
    c, h = lab["c"], lab["h"]
    task = _dispatch(lab, _plan(c))[0]
    r = _record(lab, "tech", task["id"], DENSITY)
    assert r.status_code == 201, r.text
    m = r.json()["measurements"][0]
    assert m["value"] == 1.35 and "GB/T 8170" in m["note"] and m["unit"] == "g/cm³" and m["operator"] == "tech"
    assert m["review_status"] == "unreviewed"
    assert c.get(f"/api/lab/tasks/{task['id']}").json()["status"] == "in_progress"  # claimed on first entry
    bad = [({**DENSITY, "unit": "kg/m³"}, "单位"), ({**DENSITY, "value": 9.0}, "有效范围"),
           ({**DENSITY, "metric": "ph"}, "测定的是"), ({**DENSITY, "method_code": "GB/T 0000"}, "未知检测方法")]
    for entry, msg in bad:
        r = _record(lab, "tech", task["id"], entry)
        assert r.status_code == 422 and msg in r.json()["detail"], (entry, r.text)
    assert _record(lab, "tech2", task["id"], DENSITY).status_code == 403  # someone else's task


def test_instrument_calibration_blocks_or_flags(lab, monkeypatch):
    c, h = lab["c"], lab["h"]
    task = _dispatch(lab, _plan(c))[0]
    past = (datetime.now() - timedelta(days=3)).isoformat()
    instr = c.post("/api/lab/instruments", headers=h["qa"], json={"asset_no": "BZP-01", "name": "比重瓶",
                                                                   "calibration_due": past}).json()
    assert instr["calibration_state"] == "overdue"
    r = _record(lab, "tech", task["id"], {**DENSITY, "instrument_id": instr["id"]})
    assert r.status_code == 422 and "校准" in r.json()["detail"]
    monkeypatch.setenv("FORMUNEX_LAB_BLOCK_UNCALIBRATED", "false")
    get_settings.cache_clear()
    r = _record(lab, "tech", task["id"], {**DENSITY, "instrument_id": instr["id"]})
    assert r.status_code == 201 and any("过期" in w for w in r.json()["warnings"])


def test_outliers_are_flagged_within_the_plan(lab):
    c = lab["c"]
    tasks = _dispatch(lab, _plan(c, factors=4), metrics=["density_g_cm3"])  # L8: eight tasks
    for t, v in zip(tasks[:6], [1.30, 1.31, 1.32, 1.31, 1.30, 1.32], strict=True):
        assert not _record(lab, "tech", t["id"], {**DENSITY, "value": v}).json()["warnings"]
    r = _record(lab, "tech", tasks[6]["id"], {**DENSITY, "value": 1.95}).json()
    assert any("疑似异常值" in w for w in r["warnings"]) and "疑似异常值" in r["measurements"][0]["note"]


# --------------------------------------------------------------------------- review / amend / pick


def test_review_flow_mirror_amendment_and_picks(lab, monkeypatch):
    c, h = lab["c"], lab["h"]
    task = _dispatch(lab, _plan(c))[0]
    d = _record(lab, "tech", task["id"], DENSITY).json()["measurements"][0]
    r = c.post(f"/api/lab/tasks/{task['id']}/submit", headers=h["tech"])
    assert r.status_code == 422 and "salt_spray_hours" in r.json()["detail"]
    s = _record(lab, "tech", task["id"], SALT).json()["measurements"][0]
    assert c.post(f"/api/lab/tasks/{task['id']}/submit", headers=h["tech"]).json()["status"] == "pending_review"
    assert len(c.get("/api/lab/review-queue").json()) == 2

    # The operator cannot review their own values; the technician role has no review permission anyway.
    assert c.post(f"/api/lab/measurements/{d['id']}/review", headers=h["tech"], json={"decision": "accept"}).status_code == 403
    assert c.post(f"/api/lab/measurements/{d['id']}/review", headers=h["qa"],
                  json={"decision": "reject"}).status_code == 422  # reason required
    assert c.post(f"/api/lab/measurements/{d['id']}/review", headers=h["qa"], json={"decision": "accept"}).status_code == 200
    detail = c.get(f"/api/lab/tasks/{task['id']}").json()
    assert detail["measured"] == {"density_g_cm3": 1.35} and detail["status"] == "pending_review"
    c.post(f"/api/lab/measurements/{s['id']}/review", headers=h["qa"], json={"decision": "accept"})
    detail = c.get(f"/api/lab/tasks/{task['id']}").json()
    assert detail["status"] == "done" and detail["measured"]["salt_spray_hours"] == 480

    # Amending a reviewed value keeps the original and sends it back to review.
    r = c.post(f"/api/lab/measurements/{d['id']}/amend", headers=h["tech"], json={"value": 1.36, "reason": "抄写错误"})
    assert r.status_code == 200 and r.json()["review_status"] == "unreviewed" and r.json()["value"] == 1.36
    hist = c.get(f"/api/lab/measurements/{d['id']}/history").json()
    assert hist == [{**hist[0], "field": "value", "old": "1.35", "new": "1.36", "reason": "抄写错误"}]
    assert "density_g_cm3" not in c.get(f"/api/lab/tasks/{task['id']}").json()["measured"]
    assert c.post(f"/api/lab/measurements/{d['id']}/amend", headers=h["tech2"],
                  json={"value": 1.4, "reason": "x"}).status_code == 403

    # Repeated measurements: all kept; latest by default, mean by setting, a manual pick wins.
    c.post(f"/api/lab/measurements/{d['id']}/review", headers=h["qa"], json={"decision": "accept"})
    assert c.post(f"/api/lab/tasks/{task['id']}/measurements", headers=h["tech"],
                  json={"measurements": [DENSITY]}).status_code == 422  # task is done
    from app.db.database import default_session_factory
    from app.db.models_lab import LabTaskRow

    with default_session_factory()() as sess:
        sess.get(LabTaskRow, task["id"]).status = "in_progress"
        sess.commit()
    d2 = _record(lab, "tech", task["id"], {**DENSITY, "value": 1.40}).json()["measurements"][0]
    c.post(f"/api/lab/measurements/{d2['id']}/review", headers=h["qa"], json={"decision": "accept"})
    assert c.get(f"/api/lab/tasks/{task['id']}").json()["measured"]["density_g_cm3"] == 1.40
    monkeypatch.setenv("FORMUNEX_LAB_VALUE_STRATEGY", "mean")
    get_settings.cache_clear()
    out = c.post(f"/api/lab/measurements/{d['id']}/pick", headers=h["qa"], json={"reason": "第二次读数气泡"}).json()
    assert out["values"]["density_g_cm3"]["basis"] == "manual" and out["values"]["density_g_cm3"]["value"] == 1.36


def test_rejection_returns_the_task_to_the_operator(lab):
    c, h = lab["c"], lab["h"]
    task = _dispatch(lab, _plan(c), metrics=["density_g_cm3"])[0]
    d = _record(lab, "tech", task["id"], DENSITY).json()["measurements"][0]
    c.post(f"/api/lab/tasks/{task['id']}/submit", headers=h["tech"])
    r = c.post(f"/api/lab/measurements/{d['id']}/review", headers=h["qa"], json={"decision": "reject", "reason": "比重瓶未恒温"})
    assert r.json()["review_status"] == "rejected"
    assert c.get(f"/api/lab/tasks/{task['id']}").json()["status"] == "in_progress"
    assert _record(lab, "tech", task["id"], DENSITY).status_code == 201


# --------------------------------------------------------------------------- instrument files


def test_parsers_layouts_encodings_and_aggregation():
    csv_gbk = "Sample,L*,a*,b*,dE\nS-1,90.1,0.2,1.1,0.5\nS-1,90.3,0.2,1.3,0.7\nS-2,88,0.1,2,1.2\n".encode("gb18030")
    readings, warnings = parsers.parse(csv_gbk, parsers.BUILTIN_TEMPLATES["colorimeter_lab"])
    by = {(r.sample_no, r.metric): r for r in readings}
    assert by[("S-1", "color_l")].value == pytest.approx(90.2) and by[("S-1", "color_l")].readings == 2
    assert by[("S-1", "delta_e")].value == pytest.approx(0.6) and not warnings
    long_csv = "sample_no;metric;value;unit\nA;density_g_cm3;1,35;g/cm³\nA;ph;x;\n".encode()
    readings, warnings = parsers.parse(long_csv, {**parsers.BUILTIN_TEMPLATES["generic_long"], "decimal_comma": True})
    assert [(r.metric, r.value) for r in readings] == [("density_g_cm3", 1.35)] and warnings
    with pytest.raises(LabError):
        parsers.parse(b"foo,bar\n1,2\n", parsers.BUILTIN_TEMPLATES["colorimeter_lab"])
    from openpyxl import Workbook
    import io

    wb = Workbook()
    ws = wb.active
    ws.append(["Sample ID", "Viscosity (cP)", "Temperature"])
    ws.append(["V-1", 1200, 25])
    ws.append(["V-1", 1250, 25])
    buf = io.BytesIO()
    wb.save(buf)
    readings, _w = parsers.parse(buf.getvalue(), parsers.BUILTIN_TEMPLATES["viscometer_rotational"], filename="v.xlsx")
    assert [(r.sample_no, r.metric, r.value) for r in readings] == [("V-1", "viscosity_mpas", 1250)]


def test_instrument_file_import_records_per_sample(lab):
    c, h = lab["c"], lab["h"]
    tasks = _dispatch(lab, _plan(c), metrics=["delta_e"])
    rows = "\n".join(f"{t['sample_no']},90,0.1,1,{0.4 + i / 10}" for i, t in enumerate(tasks[:2]))
    data = f"Sample,L*,a*,b*,ΔE\n{rows}\nNOPE-S1,1,1,1,1\n".encode("gb18030")
    r = c.post("/api/lab/measurements/import", headers=h["tech"], files={"file": ("cm.csv", data, "text/csv")},
               data={"template": "colorimeter_lab", "methods": json.dumps({"delta_e": "GB/T 11186.3"})})
    assert r.status_code == 201, r.text
    out = r.json()
    recorded = {m["metric"] for m in out["measurements"]}
    assert recorded == {"delta_e"} and len(out["measurements"]) == 2  # only metrics given a method are recorded
    assert any("已跳过 color_a, color_b, color_l" in w for w in out["warnings"])
    assert any(e["sample_no"] == "NOPE-S1" for e in out["errors"])
    assert all(m["raw_file_key"] == out["raw_file_key"] for m in out["measurements"])


# --------------------------------------------------------------------------- feeds


def test_sds_candidates_and_conformal_from_lab(lab):
    c, h = lab["c"], lab["h"]
    preds = {i: {"salt_spray_hours": {"predicted": 500 + 10 * i, "sigma": 20}} for i in range(1, 9)}
    tasks = _dispatch(lab, _plan(c, factors=4), metrics=["salt_spray_hours", "density_g_cm3"], predictions=preds)
    for i, t in enumerate(tasks, start=1):
        ms = _record(lab, "tech", t["id"], {**SALT, "value": 500 + 10 * i + (-1) ** i * 8},
                     DENSITY).json()["measurements"]
        for m in ms:
            c.post(f"/api/lab/measurements/{m['id']}/review", headers=h["qa"], json={"decision": "accept"})
    r = c.post("/api/cockpit/conformal/from-lab", json={"metric": "salt_spray_hours", "doe_plan_id": tasks[0]["doe_plan_id"],
                                                        "predict": [{"predicted": 600}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["source"]["pairs"] == 8 and out["calibration"]["n"] == 8
    assert c.post("/api/cockpit/conformal/from-lab", json={"metric": "nope"}).status_code == 422

    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion

    with default_session_factory()() as s:
        s.add(FormulationVersion(id="fv-lab", lineage_id="l", version=1, domain="coating", name="v",
                                 created_at=datetime(2026, 10, 1), snapshot={"ingredients": []}))
        s.commit()
    pid = c.post("/api/plm/products", json={"code": "FX-LAB", "name_zh": "x"}).json()["id"]
    win = c.post("/api/cockpit/winning", headers=h["lead"], json={
        "product_id": pid, "formulation_version_id": "fv-lab", "experiment_id": tasks[0]["experiment_id"]}).json()
    cand = c.get(f"/api/plm/revisions/{win['revision_id']}/property-candidates").json()
    dens = cand["sds_properties"]["density_g_cm3"]
    assert dens["value"] == 1.35 and dens["source"] == "measured" and dens["method"] == "GB/T 6750"
    assert cand["not_sds_properties"] == ["salt_spray_hours"]


# --------------------------------------------------------------------------- ELN


class FakeElab:
    def __init__(self):
        self.experiments: dict[int, dict] = {}
        self.fail_patch = False
        self.fail_delete = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "elab-key"
        path = request.url.path
        if request.method == "POST" and path == "/api/v2/experiments":
            eid = len(self.experiments) + 1
            self.experiments[eid] = {"id": eid, "metadata": "{}", **json.loads(request.content)}
            return httpx.Response(201, headers={"Location": f"https://elab.local/api/v2/experiments/{eid}"})
        m = re.fullmatch(r"/api/v2/experiments/(\d+)", path)
        if m:
            exp = self.experiments[int(m.group(1))]
            if request.method == "PATCH":
                if self.fail_patch:
                    return httpx.Response(500)
                exp.update(json.loads(request.content))
                return httpx.Response(200, json=exp)
            if request.method == "DELETE":
                if self.fail_delete:
                    return httpx.Response(500)
                del self.experiments[int(m.group(1))]
                return httpx.Response(204)
            return httpx.Response(200, json=exp)
        if path == "/api/v2/info":
            return httpx.Response(200, json={"elabftw_version": "5.1"})
        return httpx.Response(404)

    def fill(self, eid: int, metric: str, value: str) -> None:
        meta = json.loads(self.experiments[eid]["metadata"])
        meta["extra_fields"][metric]["value"] = value
        self.experiments[eid]["metadata"] = json.dumps(meta)

    def set_unit(self, eid: int, metric: str, unit: str) -> None:
        meta = json.loads(self.experiments[eid]["metadata"])
        meta["extra_fields"][metric]["unit"] = unit
        self.experiments[eid]["metadata"] = json.dumps(meta)


def test_elabftw_push_pull_and_conflicts(lab, monkeypatch):
    from app.services.eln import sync
    from app.services.eln.connectors.elabftw import ElabftwConnector

    c, h = lab["c"], lab["h"]
    fake = FakeElab()
    conn = ElabftwConnector("https://elab.local", "elab-key",
                            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(sync, "get_connector", lambda name: conn)
    assert conn.health()["version"] == "5.1"
    tasks = _dispatch(lab, _plan(c), metrics=["density_g_cm3"])
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [t["id"] for t in tasks[:2]]}).json()
    assert len(r["pushed"]) == 2 and r["errors"] == []
    meta = json.loads(fake.experiments[1]["metadata"])
    assert meta["extra_fields"]["density_g_cm3"]["description"] == "GB/T 6750"
    again = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [tasks[0]["id"]]}).json()
    assert again["skipped"] == [tasks[0]["task_no"]]

    pull = lambda: c.post("/api/lab/eln/elabftw/pull", headers=h["qa"], json={}).json()  # noqa: E731
    assert pull()["unchanged"] == 2
    fake.fill(1, "density_g_cm3", "1.33")
    out = pull()
    assert out["recorded"] == 1 and out["conflicts"] == 0
    queue = c.get("/api/lab/review-queue").json()
    assert queue[0]["operator"] == "elabftw" and queue[0]["external_ref"].startswith("elabftw:1:")
    assert pull()["unchanged"] == 2  # idempotent

    # Changed on both sides → conflict queue.
    c.post(f"/api/lab/measurements/{queue[0]['id']}/amend", headers=h["qa"], json={"value": 1.34, "reason": "复测"})
    fake.fill(1, "density_g_cm3", "1.31")
    assert pull()["conflicts"] == 1
    conflicts = c.get("/api/lab/eln/conflicts").json()
    assert conflicts[0]["local"] == {"density_g_cm3": 1.34} and conflicts[0]["remote"] == {"density_g_cm3": 1.31}
    r = c.post(f"/api/lab/eln/conflicts/{conflicts[0]['id']}/resolve", headers=h["qa"], json={"resolution": "take_remote"})
    assert r.json()["status"] == "resolved"
    assert pull()["unchanged"] == 2 and c.get("/api/lab/eln/conflicts").json() == []


def test_datalab_is_read_only_and_migration_moves_records(lab):
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow, MeasurementRow
    from app.db.models_lab import ElnLinkRow
    from app.services.eln.connectors.base import ConnectorError
    from app.services.eln.connectors.datalab import DatalabConnector
    from app.services.lab.datalab_migration import migrate
    from app.services.lab.measurements import sync_mirror

    with pytest.raises(ConnectorError):
        DatalabConnector("http://dl").push_plan({})
    items = {"fm_1": {"blocks_obj": {"formumind_training": {"blockype": "x", "data": {
        "domain": "anticorrosion_coating", "project_id": "p1", "factors": {"T": 150},
        "measured": {"salt_spray_hours": 720, "note": "text"}, "label": "旧实验"}}}}}
    with default_session_factory()() as s:
        s.add_all([ExperimentRow(item_id="fm_1", domain="anticorrosion_coating", project_id="", factors={}, measured={}),
                   ExperimentRow(item_id="fm_gone", domain="anticorrosion_coating", project_id="", factors={},
                                 measured={})])
        s.commit()
        dry = migrate(s, items.get, dry_run=True)
        s.rollback()
        assert dry["migrated"] == 1 and dry["missing"] == ["fm_gone"] and dry["measurements"] == 1
        report = migrate(s, items.get)
        s.commit()
        row = s.query(ExperimentRow).filter_by(label="旧实验").one()
        assert row.item_id is None and row.measured == {"salt_spray_hours": 720} and row.factors == {"T": 150}
        m = s.query(MeasurementRow).filter_by(experiment_id=row.id).one()
        assert m.review_status == "legacy" and m.external_ref == "datalab:fm_1:salt_spray_hours"
        assert s.query(ElnLinkRow).filter_by(connector="datalab", local_id=str(row.id)).count() == 1
        sync_mirror(s, row.id)  # legacy values stay in the mirror the optimiser reads
        assert row.measured == {"salt_spray_hours": 720}
        assert migrate(s, items.get)["candidates"] == 1  # only the missing one is left
        assert report["migrated"] == 1


def test_native_backend_is_the_default(monkeypatch):
    from app.config import Settings
    from app.db.store import LocalExperimentStore, get_experiment_store, reset_experiment_store

    assert Settings.model_fields["experiment_backend"].default == "native"
    reset_experiment_store(None)
    try:
        store = get_experiment_store(Settings(experiment_backend="native", campaign_backend="native"))
        assert isinstance(store, LocalExperimentStore)
    finally:
        reset_experiment_store(None)




def test_2_15_push_task_alias_calls_push_plan():
    """补齐计划 2-15：push_task 为 deprecated 别名，行为与 push_plan 一致并告警。"""
    import warnings

    from app.services.eln.connectors.base import ElnConnector, RemoteRecord

    class Fake(ElnConnector):
        name = "fake"

        def health(self):
            return {"ok": True}

        def push_plan(self, plan):
            return RemoteRecord("ext-1", "http://x/1")

        def pull_measurements(self, external_id, *, since=None):
            return []

    conn = Fake()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rec = conn.push_task({"task_no": "LT-1"})
    assert rec.external_id == "ext-1"
    assert any(issubclass(w.category, DeprecationWarning) and "push_plan" in str(w.message) for w in caught)


def test_2_15_legacy_subclass_without_push_plan_fails_loudly():
    """补齐计划 2-15（子类侧破坏性，标注）：仅实现旧 push_task 的子类实例化时 TypeError 明示。"""
    from app.services.eln.connectors.base import ElnConnector

    class Legacy(ElnConnector):
        name = "legacy"

        def health(self):
            return {"ok": True}

        def push_task(self, task):
            raise AssertionError("must not be called")

        def pull_measurements(self, external_id, *, since=None):
            return []

    with pytest.raises(TypeError, match="push_plan"):
        Legacy()


def test_datalab_campaign_rows_move_inline_and_read_back_identically(lab):
    """M7 leftover: workbench (campaign) rows living in Datalab items move into ``sample_refs``."""
    import asyncio

    from app.db.campaign_store import LocalCampaignStore, _blocks_for_row
    from app.db.database import default_session_factory
    from app.db.models import Campaign
    from app.db.models_lab import ElnLinkRow
    from app.services.lab.datalab_migration import migrate_campaigns
    from app.services.workbench_training import workbench_record_label

    def item(planned, actual, meas, status, note=None, tags=None):
        return {"refcode": "fm:abc", "blocks_obj": _blocks_for_row(planned_params=planned, actual_params=actual,
                                                                   measurements=meas, status=status, note=note,
                                                                   tags=tags)}

    items = {
        "fm_c1_r1": item({"T": 150}, {"T": 152}, {"salt_spray_hours": 720}, "Completed", "批次 A", ["复测"]),
        "fm_c1_r2": item({"T": 170}, {"T": 170}, {}, "Pending"),
    }
    factory = default_session_factory()
    with factory() as s:
        dl = Campaign(name="Datalab 工作台", sample_refs=[
            {"id": 1, "item_id": "fm_c1_r1", "parent_campaign_id": 7},
            {"id": 2, "item_id": "fm_c1_r2"},
            {"id": 3, "item_id": "fm_c1_gone"},
        ])
        native = Campaign(name="原生", sample_refs=[{"id": 1, "item_id": "local_c2_r1", "status": "Pending",
                                                    "planned_params": {"T": 1}, "actual_params": {}, "measurements": {}}])
        s.add_all([dl, native])
        s.commit()
        dl_id, native_refs = dl.id, list(native.sample_refs)

        dry = migrate_campaigns(s, items.get, dry_run=True)
        s.rollback()
        assert (dry["campaigns"], dry["rows"], dry["already_native"]) == (1, 2, 1)
        assert dry["missing"] == [{"campaign_id": dl_id, "row_id": 3, "item_id": "fm_c1_gone"}]
        assert s.get(Campaign, dl_id).sample_refs[0] == {"id": 1, "item_id": "fm_c1_r1", "parent_campaign_id": 7}

        report = migrate_campaigns(s, items.get)
        s.commit()
        assert (report["rows"], len(report["missing"]), report["pruned"]) == (2, 1, 0)
        assert s.query(ElnLinkRow).filter_by(connector="datalab", local_type="campaign_row").count() == 2

    rows = asyncio.run(LocalCampaignStore(factory).list_rows(dl_id))
    by_id = {r.id: r for r in rows}
    r1 = by_id[1]
    assert (r1.item_id, r1.status, r1.planned_params, r1.actual_params, r1.measurements, r1.note, r1.tags,
            r1.parent_campaign_id) == ("fm_c1_r1", "Completed", {"T": 150}, {"T": 152},
                                       {"salt_spray_hours": 720}, "批次 A", ["复测"], 7)
    assert by_id[2].status == "Pending" and by_id[2].planned_params == {"T": 170}
    # item_id is the training records' identity: unchanged label, no duplicate records after the switch
    assert workbench_record_label(dl_id, r1.item_id) == f"wb:{dl_id}:fm_c1_r1"

    with factory() as s:
        assert s.query(Campaign).filter_by(name="原生").one().sample_refs == native_refs  # untouched
        again = migrate_campaigns(s, items.get)  # idempotent: only the missing ref is retried
        assert again["rows"] == 0 and len(again["missing"]) == 1
        pruned = migrate_campaigns(s, items.get, prune_missing=True)
        s.commit()
        assert pruned["pruned"] == 1 and [r["id"] for r in s.get(Campaign, dl_id).sample_refs] == [1, 2]


def test_2_15_datalab_push_plan_read_only():
    """补齐计划 2-15：Datalab push_plan 同样只读报错（别名 push_task 走同一路径）。"""
    import warnings

    from app.services.eln.connectors.base import ConnectorError
    from app.services.eln.connectors.datalab import DatalabConnector

    with pytest.raises(ConnectorError, match="只读"):
        DatalabConnector("http://dl").push_plan({})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pytest.raises(ConnectorError, match="只读"):
            DatalabConnector("http://dl").push_task({})


def test_2_15_pull_since_passthrough(tmp_path):
    """补齐计划 2-15：sync.pull 的 since 透传给连接器；不传时为 None（旧调用兼容）。"""
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.models_lab import ElnLinkRow, LabTaskRow
    from app.services.eln import sync
    from app.services.eln.connectors.base import ElnConnector

    engine = make_engine(f"sqlite:///{tmp_path}/eln.db")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    task_id = str(uuid.uuid4())
    session.add(LabTaskRow(id=task_id, task_no="LT-1", status="open", project_id="proj-test"))
    session.add(ElnLinkRow(id=str(uuid.uuid4()), connector="fake", external_id="ext-1",
                           local_type="lab_task", local_id=task_id,
                           content_hash=sync._digest({}) + sync._digest({})))
    session.commit()

    seen: list = []

    class Fake(ElnConnector):
        name = "fake"

        def health(self):
            return {"ok": True}

        def push_plan(self, plan):
            raise AssertionError("not used")

        def pull_measurements(self, external_id, *, since=None):
            seen.append((external_id, since))
            return []  # 空值 → unchanged 分支，不触发 _apply（无需样品/方法行）

    conn = Fake()
    out = sync.pull(session, conn, since="2026-10-18T00:00:00Z")
    assert seen == [("ext-1", "2026-10-18T00:00:00Z")]
    assert out["checked"] == 1 and out["unchanged"] == 1
    seen.clear()
    out2 = sync.pull(session, conn)
    assert seen == [("ext-1", None)]
    assert out2["checked"] == 1 and out2["unchanged"] == 1


def test_2_14_task_attachments_upload_list_download_delete(lab):
    """补齐计划 2-14：任务附件上传 → 列表 → 下载（内容一致）→ 删除（再下载 404）。"""
    c, h = lab["c"], lab["h"]
    tasks = _dispatch(lab, _plan(c), metrics=["density_g_cm3"])
    tid = tasks[0]["id"]
    up = c.post(f"/api/lab/tasks/{tid}/attachments", headers=h["lead"],
                files={"file": ("spectrum.csv", b"a,b\n1,2\n", "text/csv")})
    assert up.status_code == 201, up.text
    att = up.json()
    assert att["filename"] == "spectrum.csv" and att["size_bytes"] == 8
    assert len(att["sha256"]) == 64
    lst = c.get(f"/api/lab/tasks/{tid}/attachments").json()
    assert [a["id"] for a in lst] == [att["id"]]
    dl = c.get(f"/api/lab/tasks/{tid}/attachments/{att['id']}/download")
    assert dl.status_code == 200 and dl.content == b"a,b\n1,2\n"
    # 不存在的任务 / 附件 → 404
    assert c.get("/api/lab/tasks/nope/attachments").status_code == 404
    assert c.get(f"/api/lab/tasks/{tid}/attachments/nope/download").status_code == 404
    rm = c.delete(f"/api/lab/tasks/{tid}/attachments/{att['id']}", headers=h["lead"])
    assert rm.status_code == 200 and rm.json()["ok"] is True
    assert c.get(f"/api/lab/tasks/{tid}/attachments").json() == []
    assert c.get(f"/api/lab/tasks/{tid}/attachments/{att['id']}/download").status_code == 404


def test_2_14_record_with_instrument_asset_no(lab):
    """补齐计划 2-14：批量录入网格填资产编号（非 UUID）也能解析仪器。"""
    c, h = lab["c"], lab["h"]
    task = _dispatch(lab, _plan(c))[0]
    instr = c.post("/api/lab/instruments", headers=h["qa"], json={"asset_no": "BZP-99", "name": "比重瓶"}).json()
    assert instr["id"] != "BZP-99"
    r = _record(lab, "tech", task["id"], {**DENSITY, "instrument_id": "BZP-99"})
    assert r.status_code == 201, r.text
    assert r.json()["measurements"][0]["instrument_id"] == instr["id"]
    # 不存在的资产编号 → 422（不静默忽略）
    task2 = _dispatch(lab, _plan(c))[0]
    r2 = _record(lab, "tech", task2["id"], {**DENSITY, "instrument_id": "NO-SUCH-ASSET"})
    assert r2.status_code == 422 and "仪器" in r2.json()["detail"]


def _elab_conn(lab, monkeypatch, fake):
    from app.services.eln import sync
    from app.services.eln.connectors.elabftw import ElabftwConnector
    conn = ElabftwConnector("https://elab.local", "elab-key",
                            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(sync, "get_connector", lambda name: conn)
    return conn


def test_elabftw_patch_failure_deletes_orphan(lab, monkeypatch):
    """W1-11: a PATCH failure after POST must not leave an orphan experiment."""
    from sqlalchemy import select

    from app.db.database import default_session_factory
    from app.db.models_lab import ElnLinkRow
    c, h = lab["c"], lab["h"]
    fake = FakeElab()
    fake.fail_patch = True
    _elab_conn(lab, monkeypatch, fake)
    tasks = _dispatch(lab, _plan(c), metrics=["density_g_cm3"])
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [tasks[0]["id"]]}).json()
    assert r["pushed"] == [] and len(r["errors"]) == 1
    assert fake.experiments == {}
    with default_session_factory()() as s:
        assert s.execute(select(ElnLinkRow)).first() is None
    fake.fail_patch = False
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [tasks[0]["id"]]}).json()
    assert len(r["pushed"]) == 1


def test_elabftw_patch_and_delete_failure_names_orphan(lab, monkeypatch):
    """W1-11: when cleanup also fails, the error names the orphan id."""
    c, h = lab["c"], lab["h"]
    fake = FakeElab()
    fake.fail_patch = True
    fake.fail_delete = True
    _elab_conn(lab, monkeypatch, fake)
    tasks = _dispatch(lab, _plan(c), metrics=["density_g_cm3"])
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [tasks[0]["id"]]}).json()
    assert len(r["errors"]) == 1 and "孤儿" in r["errors"][0]["error"] and "1" in r["errors"][0]["error"]
    assert 1 in fake.experiments


def test_elabftw_unit_only_change_is_recorded(lab, monkeypatch):
    """W1-11: same value, new unit must not take the 'unchanged' fast path."""
    from app.db.database import default_session_factory
    from app.db.models import MeasurementRow
    c, h = lab["c"], lab["h"]
    fake = FakeElab()
    _elab_conn(lab, monkeypatch, fake)
    tasks = _dispatch(lab, _plan(c), metrics=["pencil_hardness_idx"])
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead"], json={"task_ids": [tasks[0]["id"]]}).json()
    assert len(r["pushed"]) == 1
    fake.fill(1, "pencil_hardness_idx", "5")
    pull = lambda: c.post("/api/lab/eln/elabftw/pull", headers=h["qa"], json={}).json()  # noqa: E731
    assert pull()["recorded"] == 1
    fake.set_unit(1, "pencil_hardness_idx", "H")
    out = pull()
    assert out["recorded"] == 1 and out["unchanged"] == 0
    with default_session_factory()() as s:
        from sqlalchemy import select
        rows = s.execute(select(MeasurementRow).where(
            MeasurementRow.external_ref.like("elabftw:1:%")).order_by(MeasurementRow.created_at)).scalars().all()
        assert rows[-1].unit == "H"
