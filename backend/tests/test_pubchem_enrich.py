"""PubChem GHS enrichment (补齐计划 1-6).

PubChem responses are faked — no real network in tests. The fake payloads
mirror the real PUG-View shape (Record > Section > GHS Classification >
GHS Hazard Classes / GHS Hazard Statements).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.plm.regulatory.pubchem_enrich import (
    PubChemNotFound,
    PubChemUnavailable,
    apply_pubchem_enrichment,
    enrich_substance,
    fetch_pubchem_ghs,
)

def _info(name: str, *strings: str) -> dict:
    return {"Name": name,
            "Value": {"StringWithMarkup": [{"String": s} for s in strings]}}


def _ghs_view(*blocks: list[dict]) -> dict:
    """A PUG-View record whose GHS Classification section holds the given
    notifier blocks in order (flat Information list, as the live API does)."""
    infos: list[dict] = []
    for block in blocks:
        infos.extend(block)
    return {
        "Record": {
            "RecordType": "CID",
            "RecordNumber": 887,
            "Section": [
                {"TOCHeading": "Safety and Hazards", "Section": [
                    {"TOCHeading": "Hazards Identification", "Section": [
                        {"TOCHeading": "GHS Classification", "Information": infos},
                    ]},
                ]},
            ],
        }
    }


_METHANOL_BLOCK = [
    _info("Pictogram(s)", " "),
    _info("Signal", "Danger"),
    _info("GHS Hazard Statements",
          "H225: Highly flammable liquid and vapour [Danger Flammable liquids]",
          "H301: Toxic if swallowed [Danger Acute toxicity, oral]",
          "H311: Toxic in contact with skin [Danger Acute toxicity, dermal]",
          "H331: Toxic if inhaled [Danger Acute toxicity, inhalation]",
          "H370: Causes damage to organs [Danger Specific target organ toxicity, single exposure]"),
    _info("Precautionary Statement Codes", "P210, P233, P260, P280, P301+P310"),
]
# Second notifier block: disagrees (H302 instead of H301) — must be ignored.
_DISSENTING_BLOCK = [
    _info("Pictogram(s)", " "),
    _info("Signal", "Warning"),
    _info("GHS Hazard Statements",
          "H302: Harmful if swallowed [Warning Acute toxicity, oral]"),
]
METHANOL_VIEW = _ghs_view(_METHANOL_BLOCK, _DISSENTING_BLOCK)


class _FakeResp:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal httpx stand-in: routes by URL substring."""

    def __init__(self, routes: list[tuple[str, _FakeResp | Exception]]):
        self._routes = routes
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        for needle, outcome in self._routes:
            if needle in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return _FakeResp(404, {})

    def close(self):
        pass


def _methanol_client():
    return _FakeClient([
        ("cids/JSON", _FakeResp(200, {"IdentifierList": {"CID": [887]}})),
        ("pug_view", _FakeResp(200, METHANOL_VIEW)),
    ])


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Non-API tests use _session() directly — point them at a throwaway DB
    instead of the developer database."""
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'pubchem-direct.db'}")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'pubchem.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as session:
        sync_builtin_roles(session)
        # rd_engineer carries knowledge.write, which the RBAC rules require
        # for POST /api/plm/substances/* (sds-import, pubchem-enrich).
        accounts.create_user(session, username="tester", password="Test-123456",
                             roles=["rd_engineer"])
        session.commit()
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/identity/login", json={"username": "tester", "password": "Test-123456"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield client, headers
    get_settings.cache_clear()


def _session():
    from app.db.database import default_session_factory

    return default_session_factory()()


def _mk_substance(session, cas="67-56-1", name_en="methanol"):
    from app.db.models_regulatory import SubstanceRow

    sub = SubstanceRow(id=str(uuid.uuid4()), cas_no=cas, name_zh="", name_en=name_en, aliases=[])
    session.add(sub)
    session.flush()
    return sub


def test_english_map_values_are_real_codes():
    """Guard: every ENGLISH_CLASS_NAMES value must be a canonical code in
    BY_CODE, else the bracket-class filter in _parse_ghs_section silently
    drops the classification (H331 lesson)."""
    from app.services.plm.regulatory.hazard_classes import BY_CODE, ENGLISH_CLASS_NAMES

    bad = [(k, v) for k, v in ENGLISH_CLASS_NAMES.items() if v not in BY_CODE]
    assert bad == []


def test_fetch_parses_pug_view():
    result = fetch_pubchem_ghs("67-56-1", client=_methanol_client())
    assert result.cid == 887
    assert result.signal == "Danger"
    assert result.h_codes == ["H225", "H301", "H311", "H331", "H370"]
    assert result.p_codes == ["P210", "P233", "P260", "P280", "P301", "P310"]
    by_class = {c.hazard_class: c for c in result.classifications}
    assert by_class["flammable_liquid"].category == "2"
    assert by_class["flammable_liquid"].h_code == "H225"
    assert by_class["acute_tox_oral"].category == "3"
    assert by_class["stot_se"].category == "1"
    assert result.unmapped == []
    # The dissenting second notifier block (H302) is ignored: first block wins.
    assert "H302" not in result.h_codes


def test_fetch_statement_footnote_markers():
    """Real PUG-View prefixes carry footnote markers (H370 **:, H301 (99.9%):)."""
    view = _ghs_view([
        _info("Pictogram(s)", " "),
        _info("Signal", "Danger"),
        _info("GHS Hazard Statements",
              "H370 **: Causes damage to organs "
              "[Danger Specific target organ toxicity, single exposure]",
              "H301 (99.9%): Toxic if swallowed [Danger Acute toxicity, oral]"),
    ])
    client = _FakeClient([
        ("cids/JSON", _FakeResp(200, {"IdentifierList": {"CID": [123]}})),
        ("pug_view", _FakeResp(200, view)),
    ])
    result = fetch_pubchem_ghs("test", client=client)
    by_class = {c.hazard_class: c for c in result.classifications}
    assert by_class["stot_se"].category == "1"
    assert by_class["acute_tox_oral"].category == "3"
    assert result.unmapped == []
    view = _ghs_view([
        _info("Pictogram(s)", " "),
        _info("Signal", "Danger"),
        _info("GHS Hazard Statements",
              "H300: Fatal if swallowed [Danger Acute toxicity, oral]"),
    ])
    client = _FakeClient([
        ("cids/JSON", _FakeResp(200, {"IdentifierList": {"CID": [123]}})),
        ("pug_view", _FakeResp(200, view)),
    ])
    result = fetch_pubchem_ghs("test", client=client)
    assert len(result.classifications) == 1
    assert result.classifications[0].hazard_class == "acute_tox_oral"
    assert result.classifications[0].category == "1"  # H300 covers 1 and 2
    assert any("H300" in r for r in result.review_reasons)


def test_fetch_stot_se3_qualifier_from_h_code():
    view = _ghs_view([
        _info("Pictogram(s)", " "),
        _info("Signal", "Warning"),
        _info("GHS Hazard Statements",
              "H336: May cause drowsiness or dizziness "
              "[Warning Specific target organ toxicity, single exposure]"),
    ])
    client = _FakeClient([
        ("cids/JSON", _FakeResp(200, {"IdentifierList": {"CID": [123]}})),
        ("pug_view", _FakeResp(200, view)),
    ])
    result = fetch_pubchem_ghs("test", client=client)
    assert len(result.classifications) == 1
    assert result.classifications[0].hazard_class == "stot_se"
    assert result.classifications[0].category == "3"
    assert result.classifications[0].qualifier == "（麻醉效应）"


def test_fetch_unknown_compound_is_terminal():
    client = _FakeClient([("cids/JSON", _FakeResp(404, {}))])
    with pytest.raises(PubChemNotFound):
        fetch_pubchem_ghs("999-99-9", client=client)


def test_fetch_no_ghs_section_is_terminal():
    client = _FakeClient([
        ("cids/JSON", _FakeResp(200, {"IdentifierList": {"CID": [1]}})),
        ("pug_view", _FakeResp(200, {"Record": {"Section": []}})),
    ])
    with pytest.raises(PubChemNotFound, match="no GHS"):
        fetch_pubchem_ghs("water", client=client)


def test_fetch_transport_error_is_retryable():
    client = _FakeClient([("cids/JSON", httpx.ConnectError("boom", request=None))])
    with pytest.raises(PubChemUnavailable):
        fetch_pubchem_ghs("67-56-1", client=client)


def test_fetch_server_error_is_retryable():
    client = _FakeClient([("cids/JSON", _FakeResp(500, {}))])
    with pytest.raises(PubChemUnavailable):
        fetch_pubchem_ghs("67-56-1", client=client)


def test_apply_replaces_unlocked_keeps_locked():
    from app.db.models_regulatory import SubstanceClassificationRow

    with _session() as session:
        sub = _mk_substance(session)
        locked = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="carcinogenicity",
            category="1B", h_code="H350", source="pubchem",
            locked_by="ehs-1",
            locked_at=datetime.now(timezone.utc),
        )
        stale = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="flammable_liquid",
            category="1", h_code="H224", source="pubchem",
        )
        catalog = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="flammable_liquid",
            category="2", h_code="H225", source="cn_catalog",
        )
        session.add_all([locked, stale, catalog])
        session.commit()
        result = fetch_pubchem_ghs("67-56-1", client=_methanol_client())
        outcome = apply_pubchem_enrichment(session, sub, result)
        session.commit()
        assert outcome["status"] == "enriched"
        assert outcome["written"] == 5
        assert outcome["replaced"] == 1  # only the stale pubchem row
        rows = session.query(SubstanceClassificationRow).filter(
            SubstanceClassificationRow.substance_id == sub.id).all()
        pubchem = [r for r in rows if r.source == "pubchem"]
        assert len(pubchem) == 6  # 5 new + 1 locked
        # New rows carry the pubchem confidence; the locked row keeps its own.
        assert all(r.confidence == 0.6 for r in pubchem if not r.locked_at)
        assert any(r.hazard_class == "carcinogenicity" and r.locked_at for r in pubchem)
        assert any(r.hazard_class == "flammable_liquid" and r.category == "2"
                   and r.source == "cn_catalog" for r in rows)


def test_apply_empty_fetch_keeps_existing():
    from app.db.models_regulatory import SubstanceClassificationRow
    from app.services.plm.regulatory.pubchem_enrich import FetchResult

    with _session() as session:
        sub = _mk_substance(session)
        existing = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="flammable_liquid",
            category="2", h_code="H225", source="pubchem",
        )
        session.add(existing)
        session.commit()
        outcome = apply_pubchem_enrichment(
            session, sub, FetchResult(cid=887, classifications=[]))
        session.commit()
        assert outcome["status"] == "no_data" and outcome["written"] == 0
        assert session.query(SubstanceClassificationRow).count() == 1


def test_enrich_substance_end_to_end():
    with _session() as session:
        sub = _mk_substance(session)
        session.commit()
        outcome = enrich_substance(session, sub.id, client=_methanol_client())
        session.commit()
        assert outcome["status"] == "enriched" and outcome["written"] == 5
        assert outcome["query"] == "67-56-1"


def test_enrich_unknown_substance():
    with _session() as session:
        with pytest.raises(PubChemNotFound, match="not found"):
            enrich_substance(session, "no-such-id", client=_methanol_client())


def test_task_registered():
    from app.worker.tasks import run_pubchem_enrich_task

    assert run_pubchem_enrich_task.name == "formunex.pubchem_enrich"
    assert run_pubchem_enrich_task.max_retries == 3


def test_api_enrich_unknown_substance_404(api):
    client, headers = api
    r = client.post("/api/plm/substances/no-such-id/pubchem-enrich", headers=headers)
    assert r.status_code == 404, r.text
