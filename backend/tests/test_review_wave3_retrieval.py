"""Review 2026-11-26 wave 3 — retrieval P3-2 / P3-4.

P3-2 (latent): ``search_semantic_scholar`` builds its ``fieldsOfStudy`` allow
list from the resolved profile; a profile whose ``s2_fields_of_study`` is
empty yields an empty allow set, so every paper carrying ``fieldsOfStudy``
is filtered out and S2 always returns []. Fix (in ``literature.py``, landed
by the coordinator — this file is out of this agent's scope): fall back to
``_ALLOWED_S2_FIELDS`` when the profile's set is empty (including
whitespace-only entries), and log a warning.

P3-4: ``enrich_search_results`` docstring claimed the PDF quota drops the PDF
tiers (and pointed at a quota check in ``_fetch_literature_text`` that does
not exist); the code is v2 monitor-only (log a warning, keep enriching).
Docstring fixed in ``fulltext_fetcher.py`` to match the code.
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence, ProductDomain
from app.domain.search_profiles import all_profiles, get_profile
from app.services import fulltext_fetcher as ff
from app.services import literature
from app.services.pdf_downloader import FetchedFulltext


LONG_TEXT = "\n\n".join(
    f"Section {i}. Epoxy-amine coating full text paragraph with formulation details, "
    "zinc phosphate loadings, cure schedules and salt spray results measured on steel."
    for i in range(40)
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── P3-2: S2 空 profile ────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeS2Client:
    """Every page returns 15 Chemistry papers (inside the default allow set)."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        papers = [
            {
                "paperId": f"p{i}",
                "title": f"Chem {i}",
                "abstract": "epoxy coating",
                "fieldsOfStudy": ["Chemistry"],
                "externalIds": {"DOI": f"10.1234/chem{i}"},
            }
            for i in range(15)
        ]
        return _FakeResp({"data": papers})


def _with_profile(monkeypatch, profile):
    monkeypatch.setattr(
        "app.domain.search_profiles.resolve_profile", lambda domain: profile
    )
    monkeypatch.setattr(literature, "make_client", lambda **kw: _FakeS2Client())


def test_s2_empty_profile_falls_back_to_default_fields(monkeypatch):
    """空 s2_fields_of_study 的 profile 不得让 S2 恒返回 []：应回退默认集。"""
    empty = replace(
        get_profile(ProductDomain.anticorrosion_coating), s2_fields_of_study=()
    )
    _with_profile(monkeypatch, empty)
    hits = literature.search_semantic_scholar("epoxy coating", limit=5, offset=0)
    assert len(hits) == 5
    assert all(h.identifier.startswith("10.1234/chem") for h in hits)


def test_s2_whitespace_only_profile_falls_back_to_default_fields(monkeypatch):
    """仅空白条目的 s2_fields_of_study 同样视为空，回退默认集。"""
    blank = replace(
        get_profile(ProductDomain.anticorrosion_coating),
        s2_fields_of_study=("  ", ""),
    )
    _with_profile(monkeypatch, blank)
    hits = literature.search_semantic_scholar("epoxy coating", limit=5, offset=0)
    assert len(hits) == 5


def test_s2_nonempty_profile_still_filters(monkeypatch):
    """回退只针对空集：非空窄 profile 仍按自身领域过滤（回归防护）。"""
    narrow = replace(
        get_profile(ProductDomain.anticorrosion_coating),
        s2_fields_of_study=("astrophysics",),
    )
    _with_profile(monkeypatch, narrow)
    hits = literature.search_semantic_scholar("epoxy coating", limit=5, offset=0)
    assert hits == []


def test_all_builtin_profiles_have_s2_fields():
    """现存 profile 均非空的前提断言（报告核实为 latent 的依据）。"""
    for prof in all_profiles():
        assert prof.s2_fields_of_study, f"{prof.domain} has empty s2_fields_of_study"


# ── P3-4: enrich docstring 与代码（PDF 配额） ───────────────────────────────


def test_enrich_docstring_matches_monitor_only_quota():
    """docstring 关键口径：配额仅监控、不丢 PDF tier；无 _fetch 侧配额检查。"""
    doc = ff.enrich_search_results.__doc__ or ""
    assert "monitor-only" in doc
    assert "continues unchanged" in doc
    assert "no quota check" in doc
    assert "PDF tiers are dropped" not in doc
    assert "dropped for the rest of the run" not in doc


def test_enrich_over_pdf_quota_still_enriches(monkeypatch, caplog):
    """超 PDF 配额仍继续 enrich（v2 监控口径），只记 warning，不降级。"""
    monkeypatch.setenv("FORMUNEX_FULLTEXT_ENRICH", "true")
    monkeypatch.setenv("FORMUNEX_FULLTEXT_MAX_DOCS", "8")
    monkeypatch.setenv("FORMUNEX_KB_PROJECT_PDF_QUOTA", "5")
    get_settings.cache_clear()

    class _FakeStore:
        def count_for_project(self, project_id, *, acquisition=None):
            assert acquisition == "pdf"
            return 99  # 远超配额 5

    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: _FakeStore()
    )
    monkeypatch.setattr(
        ff, "_dispatch_fetch", lambda kind, ev, t: FetchedFulltext(text=LONG_TEXT)
    )
    row = Evidence(
        source="OpenAlex",
        identifier="10.1016/j.porgcoat.2020.105678",
        title="Doc",
        snippet="abstract only",
        relevance=0.9,
    )
    with caplog.at_level(logging.WARNING, logger="app.services.fulltext_fetcher"):
        out, report = ff.enrich_search_results(
            [row], persist=False, project_id="proj-over-quota"
        )
    assert report.succeeded == 1, "超配额不得丢弃可取全文的行"
    assert len(out) > 1, "应被全文 chunk 替换，而非原样返回"
    assert "monitor-only" in caplog.text
