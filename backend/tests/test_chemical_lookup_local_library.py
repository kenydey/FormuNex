"""Local-first chemical lookup: the seeded 131k ``substances`` library is consulted
before any PubChem tier, so formulation / material auto-matching resolves locally
without a network round-trip. No HTTP is performed for a local hit.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models_regulatory import Base, SubstanceRow
from app.services import chemical_lookup


@pytest.fixture()
def _local_db(monkeypatch):
    """An isolated ``substances`` table + a session factory pointed at it."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.tables["substances"].create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        session.add_all([
            SubstanceRow(id="s1", cas_no="98-09-9", name_zh="苯磺酰氯",
                         name_en="Benzenesulfonyl chloride", mf="C6H5ClO2S"),
            SubstanceRow(id="s2", cas_no="64-17-5", name_zh="乙醇",
                         name_en="Ethanol", mf="C2H6O"),
            SubstanceRow(id="s3", cas_no="7732-18-5", name_zh="水",
                         name_en="Water", mf="H2O"),
        ])
        session.commit()
    monkeypatch.setattr("app.db.database.default_session_factory", lambda: factory)
    chemical_lookup._CACHE.clear()
    yield factory
    chemical_lookup._CACHE.clear()


@pytest.fixture()
def _no_network(monkeypatch):
    """Fail loudly if any network tier is reached; record who was tried."""
    tried: list[str] = []

    def _boom(name: str):
        def _resolver(q: str):
            tried.append(name)
            raise AssertionError(f"network tier {name} must not run on a local hit")
        return _resolver

    for attr in ("_lookup_pubchem", "_lookup_compound_synonyms", "_lookup_offline_compounds",
                 "_lookup_chemtools", "_lookup_surechembl"):
        monkeypatch.setattr(chemical_lookup, attr, _boom(attr))
    return tried


def test_local_hit_by_cas_skips_pubchem(_local_db, _no_network):
    hit = chemical_lookup.lookup_chemical("98-09-9")
    assert hit["found"] is True
    assert hit["source"] == "local_library"
    assert hit["cas"] == "98-09-9"
    assert hit["zh_name"] == "苯磺酰氯"
    assert hit["formula"] == "C6H5ClO2S"
    assert _no_network == []
    assert hit["providers_tried"] == ["catalog", "local_library"]


def test_local_hit_by_chinese_name(_local_db, _no_network):
    hit = chemical_lookup.lookup_chemical("乙醇")
    assert hit["found"] is True
    assert hit["source"] == "local_library"
    assert hit["cas"] == "64-17-5"


def test_local_hit_case_insensitive_english_name(_local_db, _no_network):
    for q in ("ethanol", "ETHANOL", "Ethanol"):
        chemical_lookup._CACHE.clear()
        hit = chemical_lookup.lookup_chemical(q)
        assert hit["found"] is True, q
        assert hit["source"] == "local_library", q
        assert hit["cas"] == "64-17-5", q


def test_excel_date_cas_query_repairs_to_stored_cas(_local_db, _no_network):
    # Seed stores the repaired CAS (98-09-9); the query may arrive in the
    # Excel-corrupted form the MSDS-Generator catalogue had (2098-09-09 00:00:00).
    hit = chemical_lookup.lookup_chemical("2098-09-09 00:00:00")
    assert hit["found"] is True
    assert hit["source"] == "local_library"
    assert hit["cas"] == "98-09-9"


def test_local_miss_falls_back_to_pubchem(_local_db, monkeypatch):
    def _fake_pubchem(q: str):
        return {"query": q, "cas": "123-45-6", "iupac_name": q, "zh_name": "",
                "formula": "", "smiles": None, "molar_mass": None,
                "found": True, "source": "pubchem"}

    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", _fake_pubchem)
    hit = chemical_lookup.lookup_chemical("ObscureTradeName-42")
    assert hit["found"] is True
    assert hit["source"] == "pubchem"
    assert hit["cas"] == "123-45-6"
    assert "local_library" in hit["providers_tried"]
    assert "pubchem" in hit["providers_tried"]


def test_db_failure_degrades_to_pubchem(_local_db, monkeypatch):
    def _boom_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.database.default_session_factory", _boom_factory)

    def _fake_pubchem(q: str):
        return {"query": q, "cas": "123-45-6", "iupac_name": q, "zh_name": "",
                "formula": "", "smiles": None, "molar_mass": None,
                "found": True, "source": "pubchem"}

    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", _fake_pubchem)
    hit = chemical_lookup.lookup_chemical("98-09-9")
    assert hit["found"] is True
    assert hit["source"] == "pubchem"


def test_local_hit_does_not_fabricate_smiles_or_molar_mass(_local_db, _no_network):
    hit = chemical_lookup.lookup_chemical("7732-18-5")
    assert hit["source"] == "local_library"
    assert hit["formula"] == "H2O"
    # The library carries no SMILES / molar mass — they stay empty, never
    # backfilled from another source under the local_library label.
    assert hit["smiles"] is None
    assert hit["molar_mass"] is None


def test_enrich_spec_maps_lookup_cas_to_cas_no(monkeypatch):
    """Pre-existing bug: ``_enrich_spec`` read ``cas_no`` from a payload that
    carries ``cas``, so CAS enrichment silently never fired."""
    from app.api import materials

    monkeypatch.setattr(
        "app.services.chemical_lookup.lookup_chemical",
        lambda q: {"cas": "64-17-5", "smiles": None, "formula": "C2H6O",
                   "zh_name": "乙醇", "molar_mass": None, "found": True, "source": "local_library"},
    )
    out = materials._enrich_spec("乙醇", {})
    assert out["cas_no"] == "64-17-5"
    assert out["formula"] == "C2H6O"
    assert out["zh_name"] == "乙醇"
    # Already-present fields are never overwritten.
    out2 = materials._enrich_spec("乙醇", {"cas_no": "7732-18-5"})
    assert "cas_no" not in out2
