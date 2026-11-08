"""Batch 3 chemistry library: P2-C14/C15/C16/C17/C18."""
from __future__ import annotations

import time

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.config import get_settings
from app.db.database import Base, default_engine, default_session_factory
from app.db.models_regulatory import SubstanceRow
from app.domain.schemas import Formulation
from app.domain.schemas.doe import Ingredient
from app.services import chemical_lookup as cl
from app.services.chemical_lookup import _alias_condition, _lookup_local_library, _lookup_pubchem
from app.services.compliance_rules import check_compliance


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path}/chem3.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    yield
    get_settings.cache_clear()


def _add(session, id, cas, zh, en, aliases):
    session.add(SubstanceRow(id=id, cas_no=cas, name_zh=zh, name_en=en, aliases=aliases, mf=""))
    session.commit()


def _form(name, cas=None, pct=10.0):
    return Formulation(name="F", domain="anticorrosion_coating",
                       ingredients=[Ingredient(name=name, role="pigment", cas_no=cas, weight_pct=pct)])


def test_substring_restricted_name_warns_not_infeasible():
    r = check_compliance(_form("Lead oxide dispersion", None))
    assert r.compliant and r.status == "warn" and any("疑似 RoHS" in x for x in r.reasons)
    # No loose single-word stem rule: benign zinc oxide must still pass.
    assert check_compliance(_form("Zinc oxide", None)).status == "pass"


def test_local_library_alias_priority_and_ambiguity(db):
    with default_session_factory()() as s:
        _add(s, "a", "111-11-1", "测试物质A", "Test A", ["别名A"])
        _add(s, "b", "222-22-2", "同名", "Other B", [])
        _add(s, "c", "333-33-3", "其他", "同名", [])
        _add(s, "d", "444-44-4", "重名", "Dup1", [])
        _add(s, "e", "555-55-5", "重名", "Dup2", [])
    hit = _lookup_local_library("别名A")
    assert hit and hit["found"] and hit["cas"] == "111-11-1" and hit["source"] == "local_library"
    # name_zh tier outranks name_en tier.
    hit = _lookup_local_library("同名")
    assert hit and hit["cas"] == "222-22-2"
    hit = _lookup_local_library("重名")
    assert hit and hit["found"] is False and hit.get("ambiguous") is True
    assert len(hit["candidates"]) == 2


def test_alias_condition_compiles_on_both_dialects():
    from sqlalchemy import select

    from app.db.models_regulatory import SubstanceRow

    stmt = select(SubstanceRow).where(_alias_condition("xylene"))
    assert "LIKE" in str(stmt.compile(dialect=sqlite.dialect()))
    assert "LIKE" in str(stmt.compile(dialect=postgresql.dialect()))


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, props, xrefs):
        self._props = props
        self._xrefs = xrefs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        if "property/" in url:
            return _Resp(200, self._props)
        if "xrefs/" in url:
            return _Resp(200, self._xrefs)
        return _Resp(404, {})


def _pubchem(monkeypatch, props, xrefs):
    monkeypatch.setattr(cl, "make_client", lambda **kw: _Client(props, xrefs))


def test_pubchem_cid_mismatch_adopts_no_cas(monkeypatch):
    props = {"PropertyTable": {"Properties": [{"CID": 123, "IUPACName": "X", "MolecularFormula": "H2O"}]}}
    xrefs = {"InformationList": {"Information": [{"CID": 999, "RegistryNumber": ["1317-36-8"]}]}}
    _pubchem(monkeypatch, props, xrefs)
    hit = _lookup_pubchem("X")
    assert hit and hit["cas"] == "" and "cas_note" in hit


def test_pubchem_multiple_valid_cas_is_ambiguous(monkeypatch):
    props = {"PropertyTable": {"Properties": [{"CID": 123, "IUPACName": "X", "MolecularFormula": "H2O"}]}}
    xrefs = {"InformationList": {"Information": [{"CID": 123, "RegistryNumber": ["1317-36-8", "7440-43-9"]}]}}
    _pubchem(monkeypatch, props, xrefs)
    hit = _lookup_pubchem("X")
    assert hit and hit["found"] is False and hit.get("ambiguous") is True


def test_pubchem_invalid_cas_filtered(monkeypatch):
    props = {"PropertyTable": {"Properties": [{"CID": 123, "IUPACName": "X", "MolecularFormula": "H2O"}]}}
    xrefs = {"InformationList": {"Information": [{"CID": 123, "RegistryNumber": ["123-45-6", "1317-36-8"]}]}}
    _pubchem(monkeypatch, props, xrefs)
    hit = _lookup_pubchem("X")
    assert hit and hit["cas"] == "1317-36-8"


def test_negative_cache_ttl_is_short():
    cl._CACHE["c18-neg|"] = (time.time() - 301, {"found": False})
    assert cl._cache_get("c18-neg|") is None
    cl._CACHE["c18-pos|"] = (time.time() - 301, {"found": True})
    assert cl._cache_get("c18-pos|") is not None
    cl._CACHE["c18-pos-old|"] = (time.time() - 86401, {"found": True})
    assert cl._cache_get("c18-pos-old|") is None
