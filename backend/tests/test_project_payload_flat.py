"""P0-3 regression: ProjectRow.payload is the flat workspace dict.

Writes go through the real ProjectStore (which persists
``workspace.model_dump()``), then all three production reads must see the
data: redline constraints (``spec_contract.voc_limit_g_l``), the task
process window (``spec_contract.process_window``), and winning release-spec
derivation (``requirement.objectives`` + ``spec_contract.release_specs``).
Before the fix the three reads looked under a ``"workspace"`` key and
always returned empty on production data — while tests using nested
payload fixtures stayed green and masked the bug.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.config import get_settings


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'flat.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.project_store import ProjectStore

    s = ProjectStore(default_session_factory())
    created = s.create(title="flat payload project")
    s.update(created.id, {
        "requirement": {
            "domain": "anticorrosion_coating",
            "voc_limit_gpl": 420,
            "objectives": [
                {"metric": "salt_spray_hours", "display_name": "耐盐雾",
                 "is_release_indicator": True, "ref_min": 500, "unit": "h"},
            ],
        },
        "spec_contract": {
            "voc_limit_g_l": 420,
            "process_window": {"cure_temp_c": [120, 180]},
            "release_specs": [{"metric": "gloss", "label": "光泽", "unit": "GU"}],
        },
    })
    yield {"store": s, "pid": created.id}
    get_settings.cache_clear()


def test_store_writes_flat_payload(store):
    from app.db.database import default_session_factory
    from app.db.models import ProjectRow

    with default_session_factory()() as session:
        row = session.get(ProjectRow, store["pid"])
        assert "workspace" not in (row.payload or {})
        assert row.payload["spec_contract"]["voc_limit_g_l"] == 420
        assert row.payload["requirement"]["objectives"][0]["metric"] == "salt_spray_hours"


def test_three_reads_see_store_written_data(store):
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow, FormulationVersion, ProjectRow
    from app.db.models_plm import ProductRow
    from app.services.identity.rbac import build_principal
    from app.services.lab.tasks import project_process_window
    from app.services.plm import winning as winning_mod
    from app.services.project_redlines import load_project_constraints

    pid = store["pid"]
    with default_session_factory()() as session:
        # Redline constraints (spec_contract.voc_limit_g_l).
        constraints = load_project_constraints(session, pid)
        assert constraints.voc_limit_g_l == 420
        # Task process window (spec_contract.process_window).
        assert project_process_window(session, pid) == {"cure_temp_c": [120, 180]}
        # Winning release-spec derivation (objectives + contract specs).
        session.add(ProductRow(id="prod-x", code="FX-X", name_zh="X", project_id=pid))
        session.add(FormulationVersion(id="fv-x", lineage_id="l", version=1, domain="coating", name="v1",
                                       created_at=datetime(2026, 10, 1),
                                       snapshot={"ingredients": [{"name": "水", "weight_pct": 100}]}))
        session.add(ExperimentRow(id=1, project_id=pid, domain="coating", measured={}))
        session.commit()
        principal = build_principal("u1", ["project_lead"], {})
        rev, _ = winning_mod.select_winning(
            session, product_id="prod-x", formulation_version_id="fv-x", experiment_id=1,
            user_id="u1", principal=principal)
        by_metric = {r["metric"]: r for r in (rev.spec_snapshot or {}).get("release_specs", [])}
        assert by_metric["gloss"]["label"] == "光泽"  # 契约显式保留
        assert by_metric["salt_spray_hours"]["label"] == "耐盐雾"  # 目标派生
        assert by_metric["salt_spray_hours"]["min"] == 500
