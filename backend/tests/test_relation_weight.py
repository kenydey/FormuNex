"""WeKnora 第一批 2.5：关系权重纯函数 + 排序标注 + Neo4j 白名单测试。"""

from __future__ import annotations

import math

import pytest

from app.config import Settings, get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.services.kg.graph_query import (
    discover_substitutes,
    find_path,
    get_entity_relations,
)
from app.services.kg.relation_weight import (
    compute_pair_weights,
    decayed_weight,
    relation_weight,
    resolve_coefficients,
)


def test_relation_weight_and_decay():
    assert relation_weight(pmi_norm=1.0, strength=0.9) == pytest.approx(0.96)
    assert relation_weight(pmi_norm=0.0, strength=0.3) == pytest.approx(0.12)
    assert decayed_weight(0.8, hops=1) == pytest.approx(0.8)
    assert decayed_weight(0.8, hops=2) == pytest.approx(0.4)
    assert decayed_weight(0.8, hops=3) == pytest.approx(0.2)
    assert resolve_coefficients(0.5, 0.4) == (0.6, 0.4)
    assert resolve_coefficients(0.7, 0.3) == (0.7, 0.3)


def test_pmi_hand_computed():
    # chunk：k1:{A,B} k2:{A,B} k3:{X} k4:{Y} k5:{Z} k6:{W}，total=6。
    # PMI_AB = log((3/6)/((3/6)*(3/6))) = log 2；PMI_AC = log((1/6)/((3/6)*(2/6))) = 0。
    entity_chunks = {
        "A": {"k1", "k2"}, "B": {"k1", "k2"}, "C": {"k3"},
        "X": {"k3"}, "Y": {"k4"}, "Z": {"k5"}, "W": {"k6"},
    }
    out = compute_pair_weights(
        [("A", "B", 0.8), ("A", "C", 0.6)], entity_chunks, 6,
    )
    assert out[("A", "B")] == pytest.approx(0.6 * 1.0 + 0.4 * 0.9)
    assert out[("A", "C")] == pytest.approx(0.4 * 0.3)
    assert out[("A", "B")] > out[("A", "C")]


def test_settings_coefficient_fallback():
    s = Settings(kg_relation_pmi_weight=0.5, kg_relation_strength_weight=0.4)
    assert (s.kg_relation_pmi_weight, s.kg_relation_strength_weight) == (0.6, 0.4)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_w.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


def _seed_weight_case(store):
    """A 连 B（conf 0.6，高共现）与 C（conf 0.9，无共现）；
    旧排序 C 在前，权重排序 B 在前。"""
    with store._session_factory() as session:
        for eid, name in (("chem:a", "Alpha"), ("chem:b", "Beta"), ("chem:c", "Gamma")):
            store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:a", dst_entity_id="chem:b",
            link_type="substitutes", confidence=0.6,
            evidence_ref={"source_id": "s1", "chunk_id": "k1", "sentence": "s",
                          "confidence": 0.6, "extraction_method": "rule"},
        )
        store.merge_semantic_link(
            session, src_entity_id="chem:a", dst_entity_id="chem:c",
            link_type="synergizes", confidence=0.9,
            evidence_ref={"source_id": "s1", "chunk_id": "k3", "sentence": "s",
                          "confidence": 0.9, "extraction_method": "rule"},
        )
        for eid, chunk in (("chem:a", "k1"), ("chem:b", "k1"),
                           ("chem:a", "k2"), ("chem:b", "k2"),
                           ("chem:c", "k3")):
            store.add_mention(session, entity_id=eid, source_id="s1",
                              chunk_id=chunk, surface_form=eid)
        session.commit()


def test_get_entity_relations_weight_sort_and_switch_off(entity_store, monkeypatch):
    _seed_weight_case(entity_store)
    settings = get_settings()
    views_off = get_entity_relations("chem:a")
    assert [v.target_entity_id for v in views_off] == ["chem:c", "chem:b"]
    assert all(v.weight is None for v in views_off)

    monkeypatch.setattr(settings, "kg_relation_weight_enabled", True)
    views_on = get_entity_relations("chem:a")
    assert [v.target_entity_id for v in views_on] == ["chem:b", "chem:c"]
    assert views_on[0].weight == pytest.approx(0.32)
    assert views_on[1].weight == pytest.approx(0.18)


def test_find_path_keeps_bfs_but_annotates_decayed_weights(entity_store, monkeypatch):
    with entity_store._session_factory() as session:
        for eid, name in (("chem:a", "Alpha"), ("chem:b", "Beta"), ("chem:c", "Gamma")):
            entity_store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        for src, dst in (("chem:a", "chem:b"), ("chem:b", "chem:c")):
            entity_store.merge_semantic_link(
                session, src_entity_id=src, dst_entity_id=dst,
                link_type="substitutes", confidence=0.8,
                evidence_ref={"source_id": "s1", "chunk_id": "k1", "sentence": "s",
                              "confidence": 0.8, "extraction_method": "rule"},
            )
        for eid, chunk in (("chem:a", "k1"), ("chem:b", "k1"),
                           ("chem:b", "k2"), ("chem:c", "k2")):
            entity_store.add_mention(session, entity_id=eid, source_id="s1",
                              chunk_id=chunk, surface_form=eid)
        session.commit()
    monkeypatch.setattr(get_settings(), "kg_relation_weight_enabled", True)
    resp = find_path("chem:a", "chem:c")
    assert resp.found is True and resp.hops == 2
    w1, w2 = (s.relation.weight for s in resp.steps)
    assert w1 is not None and w2 is not None
    # 第 2 跳按位置衰减 ×0.5：w2 的未衰减值与 w1 的边权重同为 0.8 置信度
    # 的边在各自候选集内的权重；此处两边对称，w2 应恰为 w1 的一半。
    assert w2 == pytest.approx(w1 * 0.5)


def test_discover_substitutes_weight_order(entity_store, monkeypatch):
    _seed_weight_case(entity_store)
    monkeypatch.setattr(get_settings(), "kg_relation_weight_enabled", True)
    resp = discover_substitutes("chem:a")
    # B 是 substitutes 直连候选（C 是 synergizes，不进 substitutes 候选）；
    # 本例只断言候选权重已标注且不为 None，排序覆盖见 relations 测试。
    assert resp.substitutes
    assert resp.substitutes[0].entity_id == "chem:b"
    assert resp.substitutes[0].weight is not None


def test_resolve_query_seed_cap(entity_store, monkeypatch):
    with entity_store._session_factory() as session:
        entity_store.upsert_entity(session, id="chem:cas:7779-90-0", kind="chemical",
                            canonical_name="Zinc phosphate",
                            composition_status="resolved")
        for i in range(60):
            dst = f"chem:dst{i}"
            entity_store.upsert_entity(session, id=dst, kind="chemical",
                                canonical_name=f"Dst{i}",
                                composition_status="resolved")
            entity_store.merge_semantic_link(
                session, src_entity_id="chem:cas:7779-90-0", dst_entity_id=dst,
                link_type="substitutes", confidence=0.5,
                evidence_ref={"source_id": "s1", "chunk_id": "k1", "sentence": "s",
                              "confidence": 0.5, "extraction_method": "rule"},
            )
        session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "kg_seed_max", 3)
    from app.services.kg.entity_resolver import resolve_query

    resp = resolve_query("7779-90-0", settings=settings)
    assert len(resp.expanded_entity_ids) == 3
    assert resp.expanded_entity_ids[0] == "chem:cas:7779-90-0"


# ── Neo4j B 线 ──

class _FakeSession:
    def __init__(self, calls, row):
        self.calls = calls
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))

        class _Result:
            def __init__(self, row):
                self._row = row

            def single(self):
                return self._row

        return _Result(self.row)


class _FakeDriver:
    def __init__(self, calls, row=None):
        self.calls = calls
        self.row = row

    def session(self):
        return _FakeSession(self.calls, self.row)


def test_neo4j_allowlist_blocks_injection(monkeypatch):
    from app.services import neo4j_kg

    calls: list = []
    monkeypatch.setattr(neo4j_kg, "_get_driver", lambda: _FakeDriver(calls))
    assert neo4j_kg.upsert_relationship("u1", "u2", "INHIBITS", confidence=0.7) is True
    assert len(calls) == 1
    assert "r.weight = $weight" in calls[0][0]
    assert calls[0][1]["weight"] == 0.7
    assert neo4j_kg.upsert_relationship(
        "u1", "u2", 'REL_TYPE) DETACH DELETE a --', confidence=0.7) is False
    assert len(calls) == 1  # 注入串未执行查询
    assert neo4j_kg.get_relation_by_name("a", "b", "DROP TABLE x") is None
    assert len(calls) == 1


def test_neo4j_weight_default_and_legacy_none(monkeypatch):
    from app.services import neo4j_kg

    calls: list = []
    monkeypatch.setattr(neo4j_kg, "_get_driver", lambda: _FakeDriver(calls))
    assert neo4j_kg.upsert_relationship("u1", "u2", "SYNERGIZES",
                                        confidence=0.6, weight=0.9) is True
    assert calls[0][1]["weight"] == 0.9

    row = {"confidence": 0.6, "evidence": "e", "extraction_method": "rule"}
    monkeypatch.setattr(neo4j_kg, "_get_driver", lambda: _FakeDriver([], row))
    out = neo4j_kg.get_relation_by_name("a", "b", "INHIBITS")
    assert out["weight"] is None
    row2 = dict(row, weight=0.55)
    monkeypatch.setattr(neo4j_kg, "_get_driver", lambda: _FakeDriver([], row2))
    assert neo4j_kg.get_relation_by_name("a", "b", "INHIBITS")["weight"] == 0.55

    monkeypatch.setattr(neo4j_kg, "_get_driver", lambda: None)
    assert neo4j_kg.upsert_relationship("u1", "u2", "INHIBITS") is False
    assert neo4j_kg.get_relation_by_name("a", "b", "INHIBITS") is None
