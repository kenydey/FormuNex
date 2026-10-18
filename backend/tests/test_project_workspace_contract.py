"""What the UI saves into a project, the backend must give back (round-4).

``PUT /api/projects/{id}`` merges the request into the stored payload and then runs it through
``ProjectWorkspace.model_validate`` — so every key the model does not declare is dropped on the floor,
with no error. The frontend sent ``auto_loop_max_rounds`` / ``auto_loop_round`` all along: after a reload
the user's "at most N rounds" setting was back at 5, and the round counter that bounds unattended
auto-iterations restarted at 0. ``Formulation.client_uid`` (the DOE-baseline badge identity) was dropped
the same way.

The first test is the scan, kept: any key ``buildWorkspacePayload`` sends that the model lacks fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.domain.project_workspace import ProjectWorkspace
from app.domain.schemas import Formulation
from app.main import app

TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "projectWorkspace.ts"


def _payload_keys() -> list[str]:
    text = TS.read_text(encoding="utf-8")
    body = text.split("export interface ProjectWorkspacePayload {", 1)[1].split("\n}", 1)[0]
    return re.findall(r"^\s{2}([a-z_]+)\??:", body, flags=re.MULTILINE)


@pytest.mark.skipif(not TS.is_file(), reason="frontend sources not present")
def test_every_key_the_frontend_saves_is_a_field_of_the_workspace_model():
    keys = _payload_keys()
    assert len(keys) > 30, "the interface reader found too little to mean anything"
    dropped = [k for k in keys if k not in ProjectWorkspace.model_fields]
    assert not dropped, f"saved by the UI but silently dropped by the backend: {dropped}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/ws.db")
    Base.metadata.create_all(engine)
    import app.db.project_store as ps_mod

    monkeypatch.setattr(ps_mod, "_store", ProjectStore(make_session_factory(engine)))
    yield TestClient(app)
    get_settings.cache_clear()


def test_the_auto_loop_cap_and_counter_survive_a_reload(client):
    project = client.post("/api/projects", json={"title": "loop"}).json()
    r = client.put(
        f"/api/projects/{project['id']}",
        json={"workspace": {"auto_loop_on_sync": True, "auto_loop_max_rounds": 9, "auto_loop_round": 3}},
    )
    assert r.status_code == 200, r.text
    workspace = client.get(f"/api/projects/{project['id']}").json()["workspace"]
    assert (workspace["auto_loop_max_rounds"], workspace["auto_loop_round"]) == (9, 3)


def test_a_project_saved_before_these_fields_existed_gets_the_old_defaults():
    ws = ProjectWorkspace.model_validate({"search_query": "legacy"})
    assert (ws.auto_loop_max_rounds, ws.auto_loop_round) == (5, 0)


def test_the_baseline_badge_identity_survives_a_reload(client):
    project = client.post("/api/projects", json={"title": "baseline"}).json()
    card = {
        "name": "Zn primer", "domain": "anticorrosion_coating", "ingredients": [{"name": "Zinc phosphate", "role": "inhibitor", "weight_pct": 5.0}],
        "client_uid": "uid-123",
    }
    r = client.put(f"/api/projects/{project['id']}", json={"workspace": {"leaderboard": [card], "requirement": None}})
    assert r.status_code == 200, r.text
    again = client.get(f"/api/projects/{project['id']}").json()["workspace"]
    assert again["leaderboard"][0]["client_uid"] == "uid-123"
    assert Formulation.model_validate(card).client_uid == "uid-123"


def test_2_8_derive_release_specs():
    """补齐计划 2-8：出厂指标目标派生 release_specs；显式优先；同 metric 不重复。"""
    from app.domain.project_workspace import derive_release_specs

    objectives = [
        {"metric": "salt_spray_hours", "display_name": "耐盐雾", "is_release_indicator": True, "ref_min": 500, "unit": "h"},
        {"metric": "adhesion", "is_release_indicator": False},
        {"metric": "gloss", "is_release_indicator": True, "target_value": 80, "unit": "GU"},
    ]
    explicit = [{"metric": "gloss", "label": "显式光泽", "min": 70}]
    out = derive_release_specs(objectives, explicit)
    by_metric = {r["metric"]: r for r in out}
    assert by_metric["gloss"]["label"] == "显式光泽"  # 显式优先
    assert by_metric["salt_spray_hours"] == {"metric": "salt_spray_hours", "label": "耐盐雾", "unit": "h",
                                             "min": 500, "max": None, "target": None, "method": ""}
    assert "adhesion" not in by_metric
    # ObjectiveSpec 对象同样可派生
    from app.domain.schemas import ObjectiveSpec

    out2 = derive_release_specs([ObjectiveSpec(metric="m1", is_release_indicator=True, ref_max=9)], [])
    assert out2[0]["metric"] == "m1" and out2[0]["max"] == 9


def test_2_9_diff_and_record_contract_change():
    """补齐计划 2-9：spec_contract diff 识别变化键；通知追加且 acknowledged=false；无变化不记。"""
    from app.domain.project_workspace import diff_spec_contract, record_contract_change

    assert diff_spec_contract(None, None) == []
    assert diff_spec_contract({}, {}) == []
    assert diff_spec_contract({"voc_limit_g_l": 420}, {"voc_limit_g_l": 420}) == []
    assert diff_spec_contract({"voc_limit_g_l": 420}, {"voc_limit_g_l": 300}) == ["voc_limit_g_l"]
    assert diff_spec_contract({"red_lines": []}, {"red_lines": [{"cas": "1"}]}) == ["red_lines"]
    assert diff_spec_contract({"a": 1}, {"a": 1, "b": 2}) == ["b"]

    ws: dict = {}
    assert record_contract_change(ws, []) is None
    assert "contract_change_notices" not in ws
    notice = record_contract_change(ws, ["voc_limit_g_l", "red_lines"])
    assert notice is not None
    assert notice["changed_keys"] == ["red_lines", "voc_limit_g_l"]
    assert notice["targets"] == ["doe", "gate"] and notice["acknowledged"] is False
    assert len(ws["contract_change_notices"]) == 1


def test_2_9_contract_change_notices_via_api(client):
    """补齐计划 2-9：PUT 契约变更 → 通知追加（targets DOE/门禁，未确认）；确认后 acknowledged=true。"""
    project = client.post("/api/projects", json={"title": "contract"}).json()
    pid = project["id"]
    # 首次写入契约：旧为空 → 有变化 → 记 1 条通知
    r = client.put(f"/api/projects/{pid}", json={"workspace": {"spec_contract": {"voc_limit_g_l": 420}}})
    assert r.status_code == 200, r.text
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    notices = ws.get("contract_change_notices", [])
    assert len(notices) == 1
    assert notices[0]["changed_keys"] == ["voc_limit_g_l"]
    assert notices[0]["targets"] == ["doe", "gate"] and notices[0]["acknowledged"] is False
    # 相同契约再 PUT → 无变化 → 不记新通知
    r = client.put(f"/api/projects/{pid}", json={"workspace": {"spec_contract": {"voc_limit_g_l": 420}}})
    assert r.status_code == 200, r.text
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert len(ws.get("contract_change_notices", [])) == 1
    # 改 VOC 上限 → 再记 1 条；无 spec_contract 的 PUT 不记
    r = client.put(f"/api/projects/{pid}", json={"workspace": {"spec_contract": {"voc_limit_g_l": 300}}})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/projects/{pid}", json={"workspace": {"search_query": "x"}})
    assert r.status_code == 200, r.text
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert len(ws.get("contract_change_notices", [])) == 2
    # 确认全部
    r = client.post(f"/api/projects/{pid}/contract-change-notices/acknowledge", json={})
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged"] == 2 and r.json()["pending"] == 0
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert all(n["acknowledged"] for n in ws["contract_change_notices"])
    # 再次确认 → 0 条
    r = client.post(f"/api/projects/{pid}/contract-change-notices/acknowledge", json={})
    assert r.json()["acknowledged"] == 0


def test_2_9_acknowledge_single_notice_by_at(client):
    """补齐计划 2-9：按 at 确认单条通知，其余保持未确认。"""
    project = client.post("/api/projects", json={"title": "contract2"}).json()
    pid = project["id"]
    client.put(f"/api/projects/{pid}", json={"workspace": {"spec_contract": {"voc_limit_g_l": 420}}})
    client.put(f"/api/projects/{pid}", json={"workspace": {"spec_contract": {"voc_limit_g_l": 300}}})
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert len(ws["contract_change_notices"]) == 2
    first_at = ws["contract_change_notices"][0]["at"]
    r = client.post(f"/api/projects/{pid}/contract-change-notices/acknowledge", json={"at": first_at})
    assert r.json() == {"ok": True, "acknowledged": 1, "pending": 1}
    ws = client.get(f"/api/projects/{pid}").json()["workspace"]
    assert ws["contract_change_notices"][0]["acknowledged"] is True
    assert ws["contract_change_notices"][1]["acknowledged"] is False
