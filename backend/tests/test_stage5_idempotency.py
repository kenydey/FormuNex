"""Stage 5 / SR-087: the idempotency key sorts ONLY declared
order-insensitive lists; every other list keeps its order in the hash.

Inventory (2026-11-02, 23 enqueue sites / 19 ops): the single proven
order-insensitive list is ``deps.names`` (pip installs a set). Time
series, ranked lists, DOE factor/run order, evidence order, perspectives
(with [:5] truncation) and the free-form document payloads are all
order-sensitive or UNKNOWN, so reordering them must change the key.
"""
from __future__ import annotations

from app.db.outbox_store import canonicalize, idempotency_key


def test_deps_names_order_does_not_change_the_key():
    a = idempotency_key("deps", {"names": ["numpy", "pandas"]})
    b = idempotency_key("deps", {"names": ["pandas", "numpy"]})
    assert a == b


def test_loop_rmse_history_order_changes_the_key():
    base = {"prior_rmse_history": [{"round": 1, "rmse": 0.5}, {"round": 2, "rmse": 0.4}]}
    swapped = {"prior_rmse_history": [{"round": 2, "rmse": 0.4}, {"round": 1, "rmse": 0.5}]}
    assert idempotency_key("loop", base) != idempotency_key("loop", swapped)


def test_search_source_types_order_changes_the_key():
    assert idempotency_key("search", {"source_types": ["a", "b"]}) != idempotency_key(
        "search", {"source_types": ["b", "a"]})


def test_research_sources_order_changes_the_key():
    assert idempotency_key("research_recommend", {"sources": [{"id": 1}, {"id": 2}]}) != idempotency_key(
        "research_recommend", {"sources": [{"id": 2}, {"id": 1}]})


def test_nested_requirement_objectives_order_changes_the_key():
    payload = {"requirement": {"objectives": [{"metric": "a"}, {"metric": "b"}]}}
    swapped = {"requirement": {"objectives": [{"metric": "b"}, {"metric": "a"}]}}
    assert idempotency_key("loop", payload) != idempotency_key("loop", swapped)


def test_document_generate_regulations_stay_ordered():
    # BORDERLINE in the inventory (same doc set, different row order):
    # conservative default keeps the order in the key.
    assert idempotency_key("document_generate", {"regulations": ["CN", "EU"]}) != idempotency_key(
        "document_generate", {"regulations": ["EU", "CN"]})


def test_identical_payload_still_dedupes_and_dict_key_order_never_matters():
    payload = {"a": 1, "b": {"x": [1, 2], "y": "z"}}
    assert idempotency_key("loop", payload) == idempotency_key("loop", payload)
    assert idempotency_key("loop", {"a": 1, "b": 2}) == idempotency_key("loop", {"b": 2, "a": 1})


def test_canonicalize_default_preserves_order():
    assert canonicalize({"v": [3, 1, 2]}) == {"v": [3, 1, 2]}
    assert canonicalize({"v": [3, 1, 2]}, sort_paths=frozenset({("v",)})) == {"v": [1, 2, 3]}
