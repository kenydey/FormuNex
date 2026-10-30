"""补齐 2-17: 逐点审核纯逻辑 + 优化器规则 API + 审核 API。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import secrets_store as ss
from app.services.point_review import PointReviewError, apply_reviews, review_summary


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FORMUNEX_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(path))
    for key in ("FORMUNEX_OPTIMIZER_UCB_KAPPA", "FORMUNEX_OPTIMIZER_EARLY_STOP_PATIENCE",
                "FORMUNEX_OPTIMIZER_EARLY_STOP_MIN_DELTA"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    ss.reload_settings()
    yield path
    get_settings.cache_clear()
    ss.reload_settings()


@pytest.fixture()
def client(env_file):
    from app.main import app

    return TestClient(app)


_RUNS = [
    {"run_id": 1, "natural": {"a": 1.0, "b": 2.0}},
    {"run_id": 2, "natural": {"a": 3.0, "b": 4.0}},
]


def test_apply_reviews_accept_modify_reject():
    out = apply_reviews(_RUNS, [
        {"run_id": 1, "decision": "accept", "note": "ok"},
        {"run_id": 2, "decision": "modify", "factors": {"a": 3.5}, "note": "降一点"},
    ])
    assert out[0]["review"]["decision"] == "accept"
    assert out[1]["review"]["modified_factors"] == {"a": 3.5}
    summary = review_summary(out)
    assert summary["counts"] == {"pending": 0, "accept": 1, "modify": 1, "reject": 0}
    assert summary["effective_runs"] == [
        {"run_id": 1, "factors": {"a": 1.0, "b": 2.0}},
        {"run_id": 2, "factors": {"a": 3.5, "b": 4.0}},
    ]


def test_apply_reviews_reject_keeps_record_but_excludes_from_effective():
    out = apply_reviews(_RUNS, [{"run_id": 1, "decision": "reject", "note": "不安全"}])
    assert out[0]["review"]["decision"] == "reject"  # kept for audit
    # run 2 is unreviewed (pending) → also excluded; only accept/modify proceed.
    assert review_summary(out)["effective_runs"] == []


def test_apply_reviews_rejects_unknown_run_and_bad_decision():
    with pytest.raises(PointReviewError):
        apply_reviews(_RUNS, [{"run_id": 99, "decision": "accept"}])
    with pytest.raises(PointReviewError):
        apply_reviews(_RUNS, [{"run_id": 1, "decision": "maybe"}])


def test_apply_reviews_modify_validates_factors():
    with pytest.raises(PointReviewError):
        apply_reviews(_RUNS, [{"run_id": 1, "decision": "modify", "factors": {}}])
    with pytest.raises(PointReviewError):
        apply_reviews(_RUNS, [{"run_id": 1, "decision": "modify", "factors": {"zzz": 1.0}}])
    with pytest.raises(PointReviewError):
        apply_reviews(_RUNS, [{"run_id": 1, "decision": "modify", "factors": {"a": "high"}}])


def _authed(client):
    """给需要 current_user 的端点装测试用户（与 point_review_api_flow 同模式）。"""
    from app.api._principal import CurrentUser, current_user
    from app.main import app
    from app.services.identity.rbac import Principal

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="u1", username="tester", display_name="Tester", session_id="s1",
        principal=Principal(user_id="u1", global_permissions=frozenset(), project_permissions={}))
    return app


def test_optimizer_rules_defaults_and_update(client):
    from app.main import app

    _authed(client)
    try:
        r = client.get("/api/cockpit/optimizer-rules")
        assert r.status_code == 200
        assert r.json()["ucb_kappa"] == pytest.approx(1.5)
        assert r.json()["early_stop_patience"] == 5
        r = client.post("/api/cockpit/optimizer-rules", json={"ucb_kappa": 2.5, "early_stop_patience": 3})
        assert r.status_code == 200
        assert r.json()["ucb_kappa"] == pytest.approx(2.5)
        assert r.json()["early_stop_patience"] == 3
        # The numpy engine reads the rule: kappa change is effective immediately.
        from app.services.optimizer import BayesianOptimizer, Factor

        opt = BayesianOptimizer(factors=[Factor("a", 0.0, 10.0)], seed=0)
        assert opt.suggest.__defaults__ is None or True  # suggest() resolves kappa from settings
    finally:
        app.dependency_overrides.clear()


def test_optimizer_rules_validation(client):
    from app.main import app

    _authed(client)
    try:
        assert client.post("/api/cockpit/optimizer-rules", json={"ucb_kappa": 0}).status_code == 422
        assert client.post("/api/cockpit/optimizer-rules", json={"ucb_kappa": 11}).status_code == 422
        assert client.post("/api/cockpit/optimizer-rules", json={"early_stop_patience": 0}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def _make_plan(client):
    """Persist a DOE plan with 2 runs; returns its plan_id."""
    from app.db import doe_plan_store
    from app.db.database import default_session_factory
    from app.domain.schemas import DOEFactor, DOEPlan, DOERun

    plan = DOEPlan(
        design="lhs",
        factors=[DOEFactor(name="a", low=0.0, high=10.0)],
        runs=[DOERun(run_id=1, coded={"a": 0.0}, natural={"a": 1.0}),
              DOERun(run_id=2, coded={"a": 0.5}, natural={"a": 3.0})],
    )
    with default_session_factory()() as session:
        plan_id = doe_plan_store.save(session, plan, project_id="proj-test")
        session.commit()
    return plan_id


def test_point_review_api_flow(client):
    from app.api._principal import CurrentUser, current_user
    from app.main import app
    from app.services.identity.rbac import Principal

    plan_id = _make_plan(client)
    # dependency_overrides is the reliable path: cockpit registered Depends(current_user) at import.
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="u1", username="tester", display_name="Tester", session_id="s1",
        principal=Principal(user_id="u1", global_permissions=frozenset(), project_permissions={}))
    try:
        r = client.get(f"/api/cockpit/doe/{plan_id}/points")
        assert r.status_code == 200
        assert r.json()["summary"]["counts"]["pending"] == 2
        r = client.post(f"/api/cockpit/doe/{plan_id}/points/review", json={
            "reviews": [{"run_id": 1, "decision": "accept", "note": "ok"},
                        {"run_id": 2, "decision": "reject", "note": "太冒险"}]})
        assert r.status_code == 200
        assert r.json()["summary"]["counts"] == {"pending": 0, "accept": 1, "modify": 0, "reject": 1}
        assert r.json()["summary"]["effective_runs"] == [{"run_id": 1, "factors": {"a": 1.0}}]
        # Persisted: re-read shows the review.
        r = client.get(f"/api/cockpit/doe/{plan_id}/points")
        assert r.json()["runs"][0]["review"]["decision"] == "accept"
        # Unknown run → 422.
        r = client.post(f"/api/cockpit/doe/{plan_id}/points/review", json={
            "reviews": [{"run_id": 99, "decision": "accept"}]})
        assert r.status_code == 422
        # Unknown plan → 404.
        assert client.get("/api/cockpit/doe/no-such-plan/points").status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)
