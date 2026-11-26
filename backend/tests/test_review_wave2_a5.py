"""Wave 2 (P2) A5 formulation-domain tests — review 2026-11-26.

P2-1: rule-set cache key must include a content hash, so a same-size /
same-mtime rewrite is detected (SR-059).
P2-2: every non-catalog substitution layer is stamped screened/advisory,
with a CAS light screen (RoHS/SVHC presence) where a CAS is known.
"""
from __future__ import annotations

import os

import pytest

from app.domain.schemas import ProductDomain, Requirement
from app.pipeline import reconstruct
from app.services import external_alternatives as ext_mod
from app.services.plm.ghs import ruleset as ruleset_mod
from app.services.substitution import find_substitutes


@pytest.fixture(autouse=True)
def _clear_ruleset_cache():
    ruleset_mod.load_ruleset.cache_clear()
    yield
    ruleset_mod.load_ruleset.cache_clear()


# ── P2-1: cache key carries a content hash ──────────────────────────────────


def _ruleset_yaml(name: str, foo: int) -> str:
    return f"version: {name}\nstatus: draft\nfoo: {foo}\n"


def test_ruleset_cache_detects_same_size_same_mtime_rewrite(tmp_path, monkeypatch):
    """Same mtime_ns + same size + different content must not serve stale rules."""
    name = "test-rs.v1"
    monkeypatch.setattr(ruleset_mod, "RULESET_DIR", tmp_path)
    path = tmp_path / f"{name}.yaml"
    path.write_text(_ruleset_yaml(name, 10), encoding="utf-8")

    first = ruleset_mod.load_ruleset(name)
    assert first["foo"] == 10
    # Unchanged re-load hits the cache (same object, no re-parse).
    assert ruleset_mod.load_ruleset(name) is first

    st = path.stat()
    # Same byte length, different content, mtime forced back to the old value.
    rewritten = _ruleset_yaml(name, 20)
    assert len(rewritten.encode("utf-8")) == len(_ruleset_yaml(name, 10).encode("utf-8"))
    path.write_text(rewritten, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    st2 = path.stat()
    assert st2.st_mtime_ns == st.st_mtime_ns
    assert st2.st_size == st.st_size

    second = ruleset_mod.load_ruleset(name)
    assert second["foo"] == 20
    assert second is not first


def test_ruleset_cache_still_serves_unchanged_file(tmp_path, monkeypatch):
    """A byte-identical file (even with a bumped mtime) keeps serving the cache."""
    name = "test-rs.v1"
    monkeypatch.setattr(ruleset_mod, "RULESET_DIR", tmp_path)
    path = tmp_path / f"{name}.yaml"
    path.write_text(_ruleset_yaml(name, 10), encoding="utf-8")
    first = ruleset_mod.load_ruleset(name)
    os.utime(path, ns=(10**18, 10**18 + 5))
    assert ruleset_mod.load_ruleset(name) is first


# ── P2-2: non-catalog layers are advisory ───────────────────────────────────


def _req() -> Requirement:
    return Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=420)


def _genome(req: Requirement | None = None):
    return reconstruct.genome_from_requirement(req or _req())


def _slot_of(genome, role: str) -> int:
    return next(i for i, s in enumerate(genome.slots) if s.role == role)


def _fake_external(**kwargs):
    return {
        "identity": {
            "query": kwargs["material"],
            "cas_no": "999-99-9",
            "smiles": "CCO",
            "cid": None,
            "source": "catalog",
            "resolved": True,
        },
        "external": [
            {
                "name": "Ethanol",
                "iupac_name": "ethanol",
                "cas_no": "64-17-5",
                "smiles": "CCO",
                "cid": 702,
                "formula": "C2H6O",
                "molar_mass": 46.07,
                "similarity": 0.9,
                "source": "pubchem_similar",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": "hardener",
                "note": "结构相似；未做配方 Δ 预测（入库后可再算）",
            },
            {
                "name": "Lead chromate",
                "iupac_name": "lead chromate",
                "cas_no": "7758-97-6",
                "smiles": "O=[Cr](=O)([O-])[O-].[Pb+2]",
                "cid": 24465,
                "formula": "CrO4Pb",
                "molar_mass": 323.2,
                "similarity": 0.86,
                "source": "pubchem_similar",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": "hardener",
                "note": "结构相似；未做配方 Δ 预测（入库后可再算）",
            },
            {
                "name": "No CAS External",
                "iupac_name": None,
                "cas_no": None,
                "smiles": "CCC",
                "cid": 999,
                "formula": "C3H8",
                "molar_mass": 44.1,
                "similarity": 0.85,
                "source": "pubchem_similar",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": "hardener",
                "note": "结构相似；未做配方 Δ 预测（入库后可再算）",
            },
        ],
        "external_meta": {
            "enabled": True,
            "queried": True,
            "count": 3,
            "skipped_reason": None,
            "provider": "pubchem_fastsimilarity_2d",
        },
    }


def _fake_literature(**kwargs):
    return {
        "literature": [
            {
                "name": "Fake Lit Hardener",
                "source": "kg",
                "confidence": 0.8,
                "entity_id": "chem:fake",
                "cas_no": None,
                "smiles": None,
                "role_hint": "hardener",
                "in_catalog": False,
                "catalog_name": None,
                "evidence": [],
            }
        ],
        "literature_meta": {
            "enabled": True,
            "queried": True,
            "count": 1,
            "skipped_reason": None,
            "providers": ["kg"],
        },
    }


def _fake_surechembl(**kwargs):
    return {
        "surechembl": [
            {
                "name": "Fake SureChEMBL",
                "cas_no": "71-43-2",
                "smiles": "c1ccccc1",
                "inchi_key": "UHOVQNZJYSORNB-UHFFFAOYSA-N",
                "similarity": 0.88,
                "source": "surechembl",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": "hardener",
                "note": "专利化学相似物；未做配方 Δ",
            }
        ],
        "surechembl_meta": {
            "enabled": True,
            "queried": True,
            "count": 1,
            "skipped_reason": None,
            "provider": "surechembl_api",
            "search_hash": "fake",
        },
    }


def _fake_llm(**kwargs):
    return {
        "llm": [
            {
                "name": "Fake LLM Hardener",
                "cas_no": None,
                "smiles": None,
                "similarity": None,
                "source": "chemist_rules",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": "hardener",
                "note": "AI/规则建议；未做配方 Δ",
            }
        ],
        "llm_meta": {
            "enabled": True,
            "queried": True,
            "count": 1,
            "skipped_reason": None,
            "providers": ["chemist_rules"],
        },
    }


def test_non_catalog_layers_are_marked_advisory_with_light_screen(monkeypatch):
    monkeypatch.setattr(
        "app.services.external_alternatives.fetch_external_alternatives", _fake_external
    )
    monkeypatch.setattr(
        "app.services.literature_alternatives.fetch_literature_alternatives",
        _fake_literature,
    )
    monkeypatch.setattr(
        "app.services.surechembl_alternatives.fetch_surechembl_alternatives",
        _fake_surechembl,
    )
    monkeypatch.setattr(
        "app.services.llm_alternatives.fetch_llm_alternatives", _fake_llm
    )
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=True,
        include_literature=True,
        include_surechembl=True,
        include_llm=True,
    )

    # L1 catalog candidates are screened and carry a feasibility verdict.
    assert report["candidates"]
    for cand in report["candidates"]:
        assert cand["screened"] is True
        assert cand["advisory"] is False
        assert "feasible" in cand

    # Every non-catalog row: advisory markers, no feasible impersonation.
    for key in ("external", "literature", "surechembl", "llm"):
        rows = report[key]
        assert rows, f"{key} should be non-empty with mocked fetchers"
        for row in rows:
            assert row["screened"] is False, key
            assert row["advisory"] is True, key
            assert "feasible" not in row, key
            assert "blocking_reasons" not in row, key
            assert "light_screen" in row, key

    # Light screen: benign CAS passes, RoHS CAS is flagged, no-CAS is unchecked.
    by_name = {r["name"]: r for r in report["external"]}
    ethanol = by_name["Ethanol"]["light_screen"]
    assert ethanol["checked"] is True
    assert ethanol["status"] == "pass"
    assert ethanol["rohs_hits"] == []
    chromate = by_name["Lead chromate"]["light_screen"]
    assert chromate["checked"] is True
    assert chromate["status"] == "infeasible"
    assert chromate["rohs_hits"]
    assert by_name["No CAS External"]["light_screen"] == {
        "checked": False,
        "reason": "no_cas",
    }
    # Literature / LLM rows without a CAS are marked unchecked, not screened.
    assert report["literature"][0]["light_screen"]["checked"] is False
    assert report["llm"][0]["light_screen"]["checked"] is False
    # SureChEMBL row with a CAS was light-screened (outcome depends on lists).
    assert report["surechembl"][0]["light_screen"]["checked"] is True


def test_pubchem_rows_carry_advisory_markers_at_source(monkeypatch):
    """Rows built by pubchem_similar_by_smiles itself carry the markers."""
    monkeypatch.setattr(ext_mod, "external_substitutes_enabled", lambda: True)
    ext_mod.clear_external_cache()

    def fake_get(url: str, *, timeout: float = 12.0):
        if "fastsimilarity_2d" in url and "/property/" in url:
            return {
                "PropertyTable": {
                    "Properties": [
                        {
                            "CID": 702,
                            "Title": "Ethanol",
                            "IUPACName": "ethanol",
                            "MolecularFormula": "C2H6O",
                            "MolecularWeight": 46.07,
                            "CanonicalSMILES": "CCO",
                        },
                        {
                            "CID": 887,
                            "Title": "Methanol",
                            "IUPACName": "methanol",
                            "MolecularFormula": "CH4O",
                            "MolecularWeight": 32.04,
                            "CanonicalSMILES": "CO",
                        },
                    ]
                }
            }
        if "synonyms" in url:
            return {
                "InformationList": {
                    "Information": [
                        {"CID": 887, "Synonym": ["Methyl alcohol", "67-56-1", "MeOH"]},
                    ]
                }
            }
        return None

    monkeypatch.setattr(ext_mod, "_http_get_json", fake_get)
    rows = ext_mod.pubchem_similar_by_smiles("CCO", threshold=85, limit=5)
    assert len(rows) == 1
    assert rows[0]["name"] == "Methanol"
    assert rows[0]["screened"] is False
    assert rows[0]["advisory"] is True
    assert "feasible" not in rows[0]
