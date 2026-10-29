"""Tests for incremental search and relevance filtering.

All offline-safe: online retrieval libs degrade to [].
"""
import time

from fastapi.testclient import TestClient

from app.main import app
from app.services import literature

client = TestClient(app)

_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 420,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


# ── Incremental search ────────────────────────────────────────────────────────

def test_iter_search_offline_terminates_and_filters_seed():
    """iter_search must terminate offline (no source turns up new results) and
    return only query-relevant seed evidence."""
    req = literature.Requirement(**_REQUIREMENT)
    zinc, _ = literature.iter_search("zinc phosphate", ["patents"], req=req, total_limit=300)
    cerium, _ = literature.iter_search("cerium inhibitor", ["patents"], req=req, total_limit=300)
    zinc_ids = {e.identifier for e in zinc}
    cerium_ids = {e.identifier for e in cerium}
    assert "US9982145B2" in zinc_ids       # zinc phosphate primer
    assert "EP3211048A1" in cerium_ids     # cerium-based inhibitor primer
    assert zinc_ids != cerium_ids


def test_iter_search_progress_callback_receives_results():
    req = literature.Requirement(**_REQUIREMENT)
    ticks: list[int] = []
    meta_sources: list[str | None] = []
    literature.iter_search(
        "epoxy zinc phosphate",
        ["patents"],
        req=req,
        total_limit=300,
        progress_cb=lambda partial, meta=None: (
            ticks.append(len(partial)),
            meta_sources.append(meta.get("source") if meta else None),
        ),
    )
    assert ticks, "progress_cb should fire at least once"
    assert any(s is not None for s in meta_sources), "per-source meta expected"


def test_iter_search_progress_fires_multiple_times():
    """Per-source callbacks should tick more than once when multiple sub-sources run."""
    req = literature.Requirement(**_REQUIREMENT)
    ticks: list[int] = []
    literature.iter_search(
        "zinc phosphate coating",
        ["literature", "internet"],
        req=req,
        total_limit=300,
        progress_cb=lambda partial, meta=None: ticks.append(len(partial)),
    )
    assert len(ticks) >= 2, f"expected multiple progress ticks, got {len(ticks)}"


def test_search_total_limit_caps_results():
    """A synthetic flood of evidence is capped at total_limit and stays unique."""
    flood = [
        literature.Evidence(
            source="arXiv",
            identifier=f"arxiv:{i}",
            title=f"epoxy anticorrosion study {i}",
            snippet="zinc phosphate waterborne primer salt spray",
            relevance=0.9,
        )
        for i in range(500)
    ]
    ranked, _ = literature._merge_filter_rank(flood, "epoxy anticorrosion zinc phosphate", 300)
    assert len(ranked) == 300
    ids = [e.identifier for e in ranked]
    assert len(ids) == len(set(ids))


def test_merge_filter_drops_irrelevant_web_junk():
    """Internet hits with zero keyword overlap are dropped; relevant ones stay."""
    items = [
        literature.Evidence(source="Internet", identifier="u1", title="Buy cheap shoes",
                             snippet="unrelated advertising spam", relevance=0.9),
        literature.Evidence(source="Internet", identifier="u2", title="Epoxy zinc phosphate primer",
                             snippet="waterborne anticorrosion coating", relevance=0.5),
    ]
    out, _ = literature._merge_filter_rank(items, "epoxy zinc phosphate anticorrosion", 300)
    ids = {e.identifier for e in out}
    assert "u2" in ids
    assert "u1" not in ids  # junk web hit with no query overlap is filtered


def test_search_stream_endpoint_returns_task_handle():
    r = client.post("/api/search/stream", json={
        "query": "epoxy zinc phosphate",
        "source_types": ["patents"],
        "requirement": _REQUIREMENT,
    })
    assert r.status_code == 202
    body = r.json()
    assert "task_id" in body and body["stream_url"].endswith(f"{body['task_id']}/stream")
    assert body["status_url"].endswith(body["task_id"])

    # Poll status snapshot until terminal — may hit real network when intel extras installed.
    t = None
    for _ in range(120):
        t = client.get(body["status_url"]).json()
        if t["state"] in ("completed", "failed"):
            break
        time.sleep(0.15)
    assert t["state"] == "completed"
    assert t["result"]["total"] >= 1
    assert "source_status" in t["result"]
    assert "filter_report" in t["result"]
    assert isinstance(t["result"]["filter_report"], dict)
    assert "kept" in t["result"]["filter_report"]


def test_iter_search_final_progress_includes_filter_report():
    req = literature.Requirement(**_REQUIREMENT)
    reports: list[dict] = []
    literature.iter_search(
        "zinc phosphate",
        ["patents"],
        req=req,
        total_limit=300,
        progress_cb=lambda partial, meta=None: (
            reports.append(meta.get("filter_report"))
            if meta and meta.get("final")
            else None
        ),
    )
    assert reports, "final progress tick expected"
    report = reports[-1]
    assert isinstance(report, dict)
    assert report["kept"] >= 1
    assert "dropped" in report
    assert "dropped_by_reason" in report


def test_search_default_includes_internet():
    """The default source set now includes internet (web results).

    The default moved from the request schema to settings.federated_sources;
    an empty request resolves to that set via _effective_source_types.
    """
    from app.api.search import _effective_source_types

    assert "internet" in _effective_source_types([])


def test_literature_availability_uses_or_logic():
    """lit_ok is True when any literature backend is available (OR semantics)."""
    status = literature.get_source_availability()
    lit = status["literature"]
    assert isinstance(lit["available"], bool)
    # With no keys in CI, arxiv/semanticscholar libs may still enable literature.
    if lit["available"]:
        assert lit.get("reason") is None


def test_build_patent_query_used_in_search_patents():
    req = literature.Requirement(**_REQUIREMENT)
    results = literature.search_patents(req, query="zinc phosphate", limit=5)
    assert len(results) >= 1
    zinc_ids = {e.identifier for e in results}
    assert "US9982145B2" in zinc_ids


