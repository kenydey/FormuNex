"""P1-3: relational -> Neo4j projection rebuild.

The adapter is stubbed (the sandbox has no Neo4j); the fake relationship
writer reproduces the adapter's MATCH semantics — both endpoints must
have been projected — so missing-endpoint skips are exercised honestly.
"""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import KGEntity, KGEntityLink
from app.services import neo4j_kg
from app.services.kg import neo4j_rebuild


@pytest.fixture()
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/p13.db")
    Base.metadata.create_all(engine)
    return engine


def _seed(session):
    session.add_all([
        KGEntity(id="chem:cas:7779-90-0", kind="chemical", canonical_name="Zinc phosphate",
                 cas_no="7779-90-0", smiles="[Zn+2]", supplier="Acme"),
        KGEntity(id="trade:epoxy-dgeba", kind="trade", canonical_name="Bisphenol-A epoxy",
                 zh_name="双酚A环氧树脂"),
        KGEntity(id="chem:cas:106-88-7", kind="chemical", canonical_name="Epoxybutane",
                 cas_no="106-88-7"),
    ])
    session.add_all([
        # valid link between two projected entities
        KGEntityLink(id="l1", src_entity_id="chem:cas:7779-90-0",
                     dst_entity_id="trade:epoxy-dgeba", link_type="INHIBITS",
                     confidence=0.9, evidence_refs=["doc1"], is_valid=True),
        # endpoint entity does not exist relationally
        KGEntityLink(id="l2", src_entity_id="chem:cas:7779-90-0",
                     dst_entity_id="chem:cas:000-00-0", link_type="SYNERGIZES",
                     is_valid=True),
        # invalid type: interpolated into Cypher by the adapter, must never pass
        KGEntityLink(id="l3", src_entity_id="chem:cas:7779-90-0",
                     dst_entity_id="trade:epoxy-dgeba", link_type="bad type",
                     is_valid=True),
        # explicitly invalid row (different type from l1: triplet is unique)
        KGEntityLink(id="l4", src_entity_id="chem:cas:7779-90-0",
                     dst_entity_id="trade:epoxy-dgeba", link_type="SYNERGIZES",
                     is_valid=False),
    ])
    session.commit()


@pytest.fixture()
def fakes(monkeypatch):
    calls: dict[str, list] = {"compounds": [], "relationships": [], "metas": []}
    projected: set[str] = set()
    monkeypatch.setattr(neo4j_kg, "is_enabled", lambda: True)
    monkeypatch.setattr(neo4j_kg, "ensure_schema", lambda: True)

    def compound(uid, name, **kw):
        calls["compounds"].append({"uid": uid, "name": name, **kw})
        projected.add(uid)
        return True

    def relationship(src, dst, rel_type, **kw):
        calls["relationships"].append({"src": src, "dst": dst, "rel": rel_type, **kw})
        return src in projected and dst in projected

    def meta(**kw):
        calls["metas"].append(kw)
        return True

    monkeypatch.setattr(neo4j_kg, "upsert_compound", compound)
    monkeypatch.setattr(neo4j_kg, "upsert_relationship", relationship)
    monkeypatch.setattr(neo4j_kg, "record_rebuild_meta", meta)
    return calls


def test_rebuild_projects_entities_and_valid_links(engine, fakes):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed(session)
    report = neo4j_rebuild.rebuild_from_relational(session_factory=factory)
    assert report.disabled is False and report.schema_ok is True
    assert report.entities_seen == 3 and report.entities_upserted == 3
    assert report.entities_failed == 0
    assert report.links_seen == 4 and report.links_upserted == 1
    assert report.links_skipped_missing_endpoint == 1
    assert report.links_skipped_invalid_type == 1
    assert report.links_skipped_not_valid == 1
    assert report.links_failed == 0
    # Spot-check the mapping: cas / smiles / supplier carried, kind in notes.
    zinc = next(c for c in fakes["compounds"] if c["uid"] == "chem:cas:7779-90-0")
    assert zinc["name"] == "Zinc phosphate" and zinc["cas_number"] == "7779-90-0"
    assert zinc["smiles"] == "[Zn+2]" and zinc["supplier"] == "Acme"
    assert zinc["notes"] == "kind:chemical"
    # Canonical name falls back to zh_name, then id.
    trade = next(c for c in fakes["compounds"] if c["uid"] == "trade:epoxy-dgeba")
    assert trade["name"] == "Bisphenol-A epoxy"
    rel = fakes["relationships"][0]
    assert rel == {"src": "chem:cas:7779-90-0", "dst": "trade:epoxy-dgeba", "rel": "INHIBITS",
                   "confidence": 0.9, "evidence": "doc1", "extraction_method": "rule"}
    # Rebuild source + time are stamped on the graph itself.
    assert fakes["metas"] == [{"source": "relational", "entities": 3, "links": 1,
                               "skipped": 3, "failed": 0}]


def test_rebuild_is_idempotent(engine, fakes):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed(session)
    first = neo4j_rebuild.rebuild_from_relational(session_factory=factory)
    calls_first = {k: list(v) for k, v in fakes.items()}
    second = neo4j_rebuild.rebuild_from_relational(session_factory=factory)
    assert (second.entities_upserted, second.links_upserted) == \
        (first.entities_upserted, first.links_upserted)
    # Same MERGE calls, same order, second run: no new node/edge shapes.
    assert fakes["compounds"][3:] == calls_first["compounds"]
    assert fakes["relationships"][1:] == calls_first["relationships"]


def test_rebuild_disabled_writes_nothing(engine, monkeypatch):
    monkeypatch.setattr(neo4j_kg, "is_enabled", lambda: False)
    factory = make_session_factory(engine)
    with factory() as session:
        _seed(session)
    report = neo4j_rebuild.rebuild_from_relational(session_factory=factory)
    assert report.disabled is True and report.entities_seen == 0


def test_rebuild_counts_failed_upserts_without_aborting(engine, fakes, monkeypatch):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed(session)

    def flaky_compound(uid, name, **kw):
        if uid == "trade:epoxy-dgeba":
            return False
        fakes["compounds"].append({"uid": uid, "name": name, **kw})
        return True

    monkeypatch.setattr(neo4j_kg, "upsert_compound", flaky_compound)
    report = neo4j_rebuild.rebuild_from_relational(session_factory=factory)
    assert report.entities_failed == 1 and report.entities_upserted == 2
    # The link to the failed entity is skipped (endpoint never projected),
    # not attempted against a node that does not exist — plus the link
    # whose endpoint entity never existed relationally.
    assert report.links_skipped_missing_endpoint == 2
    assert report.links_upserted == 0
