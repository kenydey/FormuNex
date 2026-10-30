"""Which classification source wins when several disagree.

Plan §5.5.1: manual (locked by an EHS specialist) > supplier SDS > China
hazardous chemicals catalogue > PubChem > inferred. Selection is per hazard
class: a supplier SDS that only covers acute toxicity does not hide the
catalogue's carcinogenicity entry.

P1-1: a rule set may override the order (``source_priority`` in its YAML).
The EU (CLP) rule set puts the harmonised Annex VI classification
(``clp_annex_vi``) right after manual, because for EU documents it is
legally binding; other rule sets keep the default order, where an unknown
source ranks last — so CLP rows can only fill gaps there, never override
curated data. A locked row always wins its hazard class regardless of order.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

SOURCE_PRIORITY: tuple[str, ...] = ("manual", "supplier_sds", "cn_catalog", "pubchem", "inferred")
SOURCE_CONFIDENCE: dict[str, float] = {
    "manual": 1.0,
    "supplier_sds": 0.95,
    "cn_catalog": 0.9,
    "clp_annex_vi": 0.95,
    "pubchem": 0.6,
    "inferred": 0.4,
}
_RANK = {s: i for i, s in enumerate(SOURCE_PRIORITY)}


def rank(source: str, order: tuple[str, ...] | None = None) -> int:
    table = {s: i for i, s in enumerate(order)} if order else _RANK
    return table.get(source, len(table))


class _Classification(Protocol):
    hazard_class: str
    source: str
    locked_at: object


def effective(
    rows: Iterable[_Classification],
    order: tuple[str, ...] | list[str] | None = None,
) -> list[_Classification]:
    """Rows from the best-ranked source for each hazard class.

    A locked row always wins its hazard class, whatever its source. All rows
    of the winning source are kept (one class can legitimately carry several
    categories, e.g. reproductive 1B + lactation). ``order`` overrides the
    default priority (a rule set's ``source_priority``); unknown sources
    rank last under either order.
    """
    wanted = tuple(order) if order else None
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
        best = min(rank(r.source, wanted) for r in group)
        out.extend(r for r in group if rank(r.source, wanted) == best)
    return out
