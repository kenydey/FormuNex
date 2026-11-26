"""Wave B1: lightweight precision@k / recall@k helpers for retrieval A/B."""
from __future__ import annotations

from app.domain.schemas import Evidence


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    head = retrieved_ids[:k]
    if not head:
        return 0.0
    hits = sum(1 for i in head if i in relevant_ids)
    return hits / float(k)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 1.0
    head = set(retrieved_ids[:k])
    return len(head & relevant_ids) / float(len(relevant_ids))


def test_precision_recall_helpers():
    retrieved = ["a", "b", "c", "d"]
    relevant = {"a", "c", "z"}
    assert precision_at_k(retrieved, relevant, 2) == 0.5
    assert abs(recall_at_k(retrieved, relevant, 3) - (2 / 3)) < 1e-9


def test_ranking_metrics_on_fake_retrieve(monkeypatch):
    """Smoke: façade order can be scored against a labeled relevant set."""
    from app.services import kb_index

    labeled_relevant = {"kb:good#c0", "kb:also#c0"}

    def fake_hybrid(query, k=4, **kwargs):
        return [
            Evidence(source="kb", identifier="kb:good#c0", title="g", snippet="x", relevance=0.9),
            Evidence(source="kb", identifier="kb:noise#c0", title="n", snippet="y", relevance=0.5),
            Evidence(source="kb", identifier="kb:also#c0", title="a", snippet="z", relevance=0.4),
        ][:k]

    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)
    monkeypatch.setattr(kb_index, "search_chunks_hybrid", fake_hybrid)
    hits = kb_index.retrieve_evidence("epoxy salt spray", k=3, mode="hybrid")
    ids = [h.identifier for h in hits]
    p = precision_at_k(ids, labeled_relevant, 3)
    r = recall_at_k(ids, labeled_relevant, 3)
    assert p > 0
    # QA-11：天花板钉死改区间断言——recall 不是恒 1.0 的期望值，而是
    # 随语料/难负例浮动的指标（下界 0.95）；金标基线本身由
    # test_golden_eval 的全量跑（Recall@3 / mean RR / nDCG@3）报告，
    # 见 test_review_wave2_b2。区间不断言"必须满分"，只防回归性塌陷。
    assert 0.95 <= r <= 1.0
