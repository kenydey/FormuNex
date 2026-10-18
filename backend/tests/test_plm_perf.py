"""PLM document rendering performance: a typical SDS (16 sections) must render in < 5 s.

Covers build (build_sds) + PDF (ReportLab) + DOCX rendering end to end, so a
regression in any stage (font resolution, pictogram drawing, table layout)
shows up here instead of only in production.
"""
from __future__ import annotations

import io
import time

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")

from app.services.plm.documents.sds_builder import SdsContext, build_sds  # noqa: E402
from app.services.plm.ghs.engine import classify  # noqa: E402
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties  # noqa: E402
from app.services.plm.render.docx import render_docx  # noqa: E402
from app.services.plm.render.pdf import render_pdf  # noqa: E402

COMPANY = {"name": "方枢示例化工有限公司", "address": "上海市示例路 1 号", "phone": "021-0000", "emergency_phone": "021-0001"}
PRODUCT = {"name_zh": "FX-100 环氧底漆", "name_en": "FX-100 Epoxy Primer", "code": "FX-100"}

# 5 s budget for the whole SDS pipeline (build + PDF + DOCX), per the dev plan.
SDS_RENDER_BUDGET_S = 5.0


def _classification():
    comps = [Component("二甲苯", 25, "1330-20-7", hazards=[ComponentHazard("flammable_liquid", "3", "H226"),
                                                         ComponentHazard("skin_corrosion", "2", "H315")]),
             Component("环氧树脂", 60, "25068-38-6", hazards=[ComponentHazard("skin_sensitizer", "1", "H317")]),
             Component("水", 15, "7732-18-5")]
    r = classify(comps, MixtureProperties(flash_point_c=27, initial_boiling_point_c=138)).to_dict()
    r["statements"] = {c: {"zh": f"{c}中文", "en": ""} for c in r["h_codes"] + r["p_codes"]}
    r["statements"]["P501"] = {"zh": "处置内装物/容器……", "en": ""}
    return r


def _sds():
    cls = _classification()
    ctx = SdsContext(product=PRODUCT, company=COMPANY, classification=cls,
                     statements=cls["statements"], components=cls["disclosure"],
                     revision={"version": "A.1", "date": "2026-10-16"})
    return build_sds(ctx)


def test_sds_rendering_fits_five_second_budget():
    start = time.perf_counter()
    sds = _sds()
    build_s = time.perf_counter() - start
    assert [s["no"] for s in sds["sections"]] == list(range(1, 17))

    start = time.perf_counter()
    pdf, _info = render_pdf(sds)
    pdf_s = time.perf_counter() - start
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000

    start = time.perf_counter()
    docx_bytes = render_docx(sds)
    docx_s = time.perf_counter() - start
    assert docx_bytes.startswith(b"PK") and len(docx_bytes) > 1000

    total = build_s + pdf_s + docx_s
    assert total < SDS_RENDER_BUDGET_S, (
        f"SDS render took {total:.2f}s (build {build_s:.2f}s, PDF {pdf_s:.2f}s, DOCX {docx_s:.2f}s), "
        f"budget {SDS_RENDER_BUDGET_S:.0f}s")


def test_sds_pdf_render_alone_fits_budget():
    """PDF alone must stay well inside the budget (guards the two-pass layout)."""
    sds = _sds()
    start = time.perf_counter()
    pdf, _info = render_pdf(sds)
    assert time.perf_counter() - start < SDS_RENDER_BUDGET_S
    assert pdf.startswith(b"%PDF")


def test_sds_render_is_deterministic_enough_to_time_repeatedly():
    """Two consecutive renders both fit: catches first-render-only caches hiding a slow path."""
    sds = _sds()
    for _ in range(2):
        start = time.perf_counter()
        pdf, _ = render_pdf(sds)
        assert time.perf_counter() - start < SDS_RENDER_BUDGET_S
        assert pdf.startswith(b"%PDF")
        docx_bytes = render_docx(sds)
        assert time.perf_counter() - start < SDS_RENDER_BUDGET_S
        assert docx_bytes.startswith(b"PK")
        _ = io.BytesIO(pdf)  # keep the import used; PDF bytes are asserted above


# ---------------------------------------------------------------------------
# Real-time compliance (50 components < 150 ms) + substance fuzzy search (< 100 ms)
#
# The 150 ms budget is the real-time editor's per-edit budget (方案 §4.6): the
# WebSocket endpoint re-classifies the whole formulation on every edit, so the
# measurement uses the same warmed-cache path (``build_and_classify(cached=True)``
# + ``to_response(cached=True)``) after one warm-up call.
#
# The 100 ms fuzzy-search budget is the production budget on PostgreSQL, where
# migration 0058 provides ``pg_trgm`` GIN indexes over 131k substance names.
# SQLite (CI only) has no trigram index, so this test seeds a smaller corpus
# (2,000 rows) and guards the query shape + no-N+1 behaviour at the same
# threshold — the production guarantee comes from the pg_trgm index, not from
# this test.
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402

from fastapi.testclient import TestClient as _TestClient  # noqa: E402

from app.config import get_settings as _get_settings  # noqa: E402

_PERF_HAZARDS = [
    ("flammable_liquid", "3", "H226"),
    ("acute_tox_oral", "4", "H302"),
    ("acute_tox_dermal", "4", "H312"),
    ("skin_corrosion", "2", "H315"),
    ("eye_damage", "2A", "H319"),
    ("aquatic_acute", "2", "H401"),
    ("aquatic_chronic", "2", "H411"),
]


def _valid_cas(i: int) -> str:
    """A syntactically valid CAS RN (correct check digit) for synthetic test data."""
    digits = f"{100000 + i}"  # 6 digits: first group 4, second group 2
    total = sum(int(d) * (len(digits) - j) for j, d in enumerate(digits))
    return f"{digits[:4]}-{digits[4:]}-{total % 10}"


@pytest.fixture()
def perf_api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-perf.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    _get_settings.cache_clear()
    from sqlalchemy import insert

    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    sub_rows, cls_rows = [], []
    for i in range(50):
        sid = str(_uuid.uuid4())
        sub_rows.append({"id": sid, "cas_no": _valid_cas(i), "name_zh": f"测试组分{i:02d}",
                         "name_en": f"test-component-{i:02d}", "aliases": [], "hazardous_catalog_no": None})
        for j, (hc, cat, h) in enumerate(_PERF_HAZARDS):
            if (i + j) % 3 == 0:
                continue
            cls_rows.append({"id": str(_uuid.uuid4()), "substance_id": sid, "hazard_class": hc,
                             "category": cat, "h_code": h, "source": "cn_catalog"})
    # Fuzzy-search corpus: 2,000 rows with realistic mixed zh/en names.
    for i in range(2000):
        sub_rows.append({"id": str(_uuid.uuid4()), "cas_no": None, "name_zh": f"测试物质{i:04d}醇",
                         "name_en": f"test-substance-{i:04d}-ol", "aliases": [],
                         "hazardous_catalog_no": None})
    with default_session_factory()() as session:
        session.execute(insert(SubstanceRow), sub_rows)
        session.execute(insert(SubstanceClassificationRow), cls_rows)
        session.commit()
    from app.main import app

    yield _TestClient(app)
    _get_settings.cache_clear()


def test_fifty_component_classification_under_150ms(perf_api):
    """Warmed-cache re-classification of a 50-component formulation (WS path) < 150 ms."""
    from app.db.database import default_session_factory
    from app.services.plm.ghs import service

    items = [{"name": f"测试组分{i:02d}", "cas": _valid_cas(i), "pct": 2.0} for i in range(50)]
    with default_session_factory()() as session:
        # Warm-up: fills the in-process regulatory-data cache (first edit).
        result, _ = service.build_and_classify(session, items, {"phys_state": "liquid"}, cached=True)
        service.to_response(session, result, cached=True)
        # Timed: the steady-state edit path the WebSocket endpoint serves.
        start = time.monotonic()
        result, _ = service.build_and_classify(session, items, {"phys_state": "liquid"}, cached=True)
        response = service.to_response(session, result, cached=True)
        elapsed = time.monotonic() - start
    assert response["classified"] is True
    assert len(response["hazards"]) > 0
    assert elapsed < 0.15, f"50-component classification took {elapsed * 1000:.1f} ms (budget 150 ms)"


def test_substance_fuzzy_search_under_100ms(perf_api):
    """Substring search over the substance corpus via the API < 100 ms."""
    # Warm-up (first query compiles the statement + warms the connection).
    r = perf_api.get("/api/plm/substances", params={"q": "测试物质1999", "limit": 20})
    assert r.status_code == 200, r.text
    start = time.monotonic()
    r = perf_api.get("/api/plm/substances", params={"q": "测试物质", "limit": 20})
    elapsed = time.monotonic() - start
    assert r.status_code == 200, r.text
    assert len(r.json()) == 20
    assert elapsed < 0.10, f"fuzzy search took {elapsed * 1000:.1f} ms (budget 100 ms)"
