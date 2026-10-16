"""Which classification source wins when several disagree.

Plan §5.5.1: manual (locked by an EHS specialist) > supplier SDS > China
hazardous chemicals catalogue > PubChem > inferred. Selection is per hazard
class: a supplier SDS that only covers acute toxicity does not hide the
catalogue's carcinogenicity entry.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

SOURCE_PRIORITY: tuple[str, ...] = ("manual", "supplier_sds", "cn_catalog", "pubchem", "inferred")
SOURCE_CONFIDENCE: dict[str, float] = {
    "manual": 1.0,
    "supplier_sds": 0.95,
    "cn_catalog": 0.9,
    "pubchem": 0.6,
    "inferred": 0.4,
}
_RANK = {s: i for i, s in enumerate(SOURCE_PRIORITY)}


class _Classification(Protocol):
    hazard_class: str
    source: str
    locked_at: object


def rank(source: str) -> int:
    return _RANK.get(source, len(SOURCE_PRIORITY))


def effective(rows: Iterable[_Classification]) -> list[_Classification]:
    """Rows from the best-ranked source for each hazard class.

    A locked row always wins its hazard class, whatever its source. All rows
    of the winning source are kept (one class can legitimately carry several
    categories, e.g. reproductive 1B + lactation).
    """
    by_class: dict[str, list[_Classification]] = {}
    for row in rows:
        by_class.setdefault(row.hazard_class, []).append(row)
    out: list[_Classification] = []
    for hazard_class in sorted(by_class):
        group = by_class[hazard_class]
        locked = [r for r in group if getattr(r, "locked_at", None)]
        if locked:
            out.extend(locked)
            continue
        best = min(rank(r.source) for r in group)
        out.extend(r for r in group if rank(r.source) == best)
    return out
