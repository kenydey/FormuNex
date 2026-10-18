"""Batch re-classification when a new GB 30000 rule set is released (plan 3-10).

A rule-set release does NOT silently rewrite history: stored
:class:`ClassificationResultRow` rows are immutable audit records. This module
answers the two questions a release triggers:

1. Which products are affected — rows whose ``rule_set_version`` is not the
   new version, grouped by product (via ``revision_id``).
2. What would change — each affected row is re-classified from its formulation
   version's ingredients under the new rule set and compared with the stored
   result (``changed`` / ``unchanged``).

``apply=True`` persists the fresh result as a *new* row (old row untouched);
documents that cited the old classification still need regeneration — the
report lists them per product so the release flow can prompt for it.

Limits, stated honestly:

- A row without ``formulation_version_id`` cannot be recomputed: the stored
  row keeps only ``inputs_hash`` + result, not the input items. Such rows are
  reported as ``needs_manual`` instead of guessed.
- Mixture properties (flash point, …) are recovered from a document that cited
  the row (``content.meta.classification_properties``). With no citing
  document they default to ``{}`` and the row is flagged
  ``properties_defaulted`` — the recompute is then a lower-bound check, not a
  byte-identical rerun.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....db.models_plm import (
    ClassificationResultRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
)
from ..ghs.ruleset import load_ruleset

# Fields compared to decide whether a re-classification changed the outcome.
_COMPARE_KEYS = ("h_codes", "p_codes", "signal_word", "pictograms", "hazards", "transport")


def _summarize(result: dict[str, Any]) -> dict[str, Any]:
    """The outcome-relevant slice of a classification result dict."""
    pictograms = result.get("pictograms") or []
    return {
        "h_codes": sorted(result.get("h_codes") or []),
        "p_codes": sorted(result.get("p_codes") or []),
        "signal_word": result.get("signal_word"),
        "pictograms": sorted(p.get("code") for p in pictograms if isinstance(p, dict)),
        "hazards": sorted(
            (h.get("hazard_class"), h.get("category"))
            for h in (result.get("hazards") or [])
            if isinstance(h, dict)
        ),
        "transport": result.get("transport"),
    }


def _citing_documents(session: Session, classification_id: str) -> list[dict[str, Any]]:
    """Documents whose stored meta cites this classification row."""
    out = []
    rows = session.execute(select(RegulatoryDocumentRow)).scalars().all()
    for d in rows:
        meta = (d.content or {}).get("meta") or {}
        if meta.get("classification_id") == classification_id:
            out.append({
                "document_id": d.id, "doc_type": d.doc_type, "lang": d.lang,
                "version_no": d.version_no, "status": d.status,
                "classification_properties": meta.get("classification_properties") or {},
            })
    return out


def find_affected_products(session: Session, version: str) -> list[dict[str, Any]]:
    """Products with stored classifications not computed under ``version``.

    Raises ``ValueError`` for an unknown rule-set version.
    """
    load_ruleset(version)  # validates the version exists
    rows = session.execute(
        select(ClassificationResultRow).where(ClassificationResultRow.rule_set_version != version)
    ).scalars().all()
    by_product: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id, product_name, revision = None, None, None
        if row.revision_id:
            revision = session.get(ProductRevisionRow, row.revision_id)
            if revision is not None:
                product = session.get(ProductRow, revision.product_id)
                product_id = revision.product_id
                product_name = product.name_zh if product is not None else None
        key = product_id or f"__no_product__:{row.id}"
        entry = by_product.setdefault(key, {
            "product_id": product_id, "product_name": product_name,
            "revision_id": row.revision_id, "classifications": [],
        })
        entry["classifications"].append({
            "classification_id": row.id,
            "old_rule_set_version": row.rule_set_version,
            "inputs_hash": row.inputs_hash,
            "formulation_version_id": row.formulation_version_id,
            "documents": _citing_documents(session, row.id),
        })
    return list(by_product.values())


def recompute_ruleset(session: Session, version: str, *, apply: bool = False) -> dict[str, Any]:
    """Re-classify every affected row under ``version`` and report the delta.

    ``apply=False`` (default) is a dry run: nothing is persisted. With
    ``apply=True`` each successfully recomputed row is persisted as a *new*
    :class:`ClassificationResultRow` (``persist``); the old row is never
    mutated. Rows that cannot be recomputed keep status ``needs_manual``.
    """
    from ..ghs import service as ghs_service

    load_ruleset(version)  # validates the version exists
    affected = find_affected_products(session, version)
    results: list[dict[str, Any]] = []
    n_changed = n_unchanged = n_manual = 0
    for product in affected:
        for cls in product["classifications"]:
            item: dict[str, Any] = {
                "classification_id": cls["classification_id"],
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                "revision_id": product["revision_id"],
                "old_rule_set_version": cls["old_rule_set_version"],
                "formulation_version_id": cls["formulation_version_id"],
                "documents": [
                    {k: d[k] for k in ("document_id", "doc_type", "lang", "version_no", "status")}
                    for d in cls["documents"]
                ],
            }
            if not cls["formulation_version_id"]:
                item["status"] = "needs_manual"
                item["reason"] = "分类结果未关联配方版本，无法从存储数据恢复输入（仅有 inputs_hash）"
                n_manual += 1
                results.append(item)
                continue
            try:
                items = ghs_service.formulation_version_items(session, cls["formulation_version_id"])
            except KeyError:
                item["status"] = "needs_manual"
                item["reason"] = f"配方版本 {cls['formulation_version_id']} 已不存在"
                n_manual += 1
                results.append(item)
                continue
            properties: dict[str, Any] = {}
            properties_defaulted = True
            for d in cls["documents"]:
                if d.get("classification_properties"):
                    properties = d["classification_properties"]
                    properties_defaulted = False
                    break
            item["properties_defaulted"] = properties_defaulted
            try:
                fresh = ghs_service.classify_items(
                    session, items, properties, ruleset=version, include_transport=True)
            except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
                item["status"] = "error"
                item["reason"] = f"重新分类失败：{exc}"
                results.append(item)
                continue
            old_row = session.get(ClassificationResultRow, cls["classification_id"])
            old_summary = _summarize(old_row.result if old_row is not None else {})
            new_summary = _summarize(fresh.to_dict())
            changed = old_summary != new_summary
            item["status"] = "changed" if changed else "unchanged"
            item["old"] = old_summary
            item["new"] = new_summary
            if changed:
                n_changed += 1
            else:
                n_unchanged += 1
            if apply:
                new_row = ghs_service.persist(
                    session, fresh, formulation_version_id=cls["formulation_version_id"],
                    revision_id=product["revision_id"])
                item["new_classification_id"] = new_row.id
                session.flush()
            results.append(item)
    return {
        "ruleset_version": version, "applied": apply,
        "affected_products": len(affected),
        "total_rows": len(results),
        "changed": n_changed, "unchanged": n_unchanged, "needs_manual": n_manual,
        "results": results,
    }
