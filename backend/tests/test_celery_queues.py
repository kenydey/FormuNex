"""开发方案 §4.6：任务队列拆分。

ingest / optimize / render / regulatory / default 5 个队列 + molscribe；
现有任务按类型路由，render / regulatory 预留。
"""
from __future__ import annotations

import pytest

pytest.importorskip("celery")

from app.worker.celery_app import celery_app  # noqa: E402

EXPECTED_QUEUES = {"ingest", "optimize", "render", "regulatory", "default", "molscribe"}


def test_queues_declared():
    names = {q.name for q in celery_app.conf.task_queues}
    assert EXPECTED_QUEUES <= names
    assert celery_app.conf.task_default_queue == "default"


def test_ingest_tasks_route_to_ingest():
    routes = celery_app.conf.task_routes
    for name in ("formunex.kb_ingest", "formunex.file_ingest", "formunex.kb_reparse",
                 "formunex.fulltext_retry", "formunex.chunk_quality_sample"):
        assert routes[name]["queue"] == "ingest", name


def test_optimize_tasks_route_to_optimize():
    routes = celery_app.conf.task_routes
    for name in ("formunex.optimize", "formunex.recommend", "formunex.loop",
                 "formunex.doe_cycle", "formunex.inverse_design", "formunex.screening"):
        assert routes[name]["queue"] == "optimize", name


def test_molscribe_stays_on_its_queue():
    assert celery_app.conf.task_routes["formunex.molscribe_recognize"]["queue"] == "molscribe"


def test_render_and_regulatory_queues_reserved():
    names = {q.name for q in celery_app.conf.task_queues}
    assert "render" in names and "regulatory" in names
    routes = celery_app.conf.task_routes
    assert all(v["queue"] not in ("render", "regulatory") for v in routes.values())


def test_unrouted_tasks_fall_to_default():
    # A task with no explicit route resolves to the default queue.
    assert celery_app.conf.task_default_queue == "default"
    assert "formunex.search" not in celery_app.conf.task_routes
    assert "formunex.deep_research" not in celery_app.conf.task_routes
