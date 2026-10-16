"""Field-level diff between two versions of a structured document.

Changes are reported per field / table row with old and new values. A change
in a hazard-bearing part (SDS sections 2, 3, 14; the label's pictograms,
signal word and statements; COA results) is *major*: it means the document
must go through sign-off again (plan §5.5.8).
"""
from __future__ import annotations

from typing import Any

MAJOR_KEYS: dict[str, set[str]] = {
    "sds": {"hazards", "composition", "transport"},
    "label": {"label.pictograms", "label.signal_word", "label.hazard_statements", "label.precautionary_statements"},
    "coa": {"results", "conclusion"},
    "tds": {"typical_properties"},
}


def flatten(content: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in content.get("sections", []):
        sk = s["key"]
        for f in s.get("fields", []):
            out[f"{sk}.{f['key']}"] = f.get("value")
        for tb in s.get("tables", []):
            for i, row in enumerate(tb.get("rows", [])):
                # Rows are keyed by their first cell (component, property, test item) so a
                # reordered table does not read as every row changed.
                out[f"{sk}.{tb['key']}[{row[0] if row else i}]"] = row
        for i, p in enumerate(s.get("paragraphs", [])):
            out[f"{sk}.paragraph[{i}]"] = p
    return out


def _is_major(doc_type: str, path: str) -> bool:
    keys = MAJOR_KEYS.get(doc_type, set())
    section = path.split(".")[0]
    return section in keys or any(path == k or path.startswith(k + "[") for k in keys)


def diff_documents(old: dict, new: dict) -> dict[str, Any]:
    doc_type = new.get("doc_type") or old.get("doc_type") or ""
    a, b = flatten(old), flatten(new)
    changes = []
    for path in sorted(set(a) | set(b)):
        if a.get(path) == b.get(path):
            continue
        kind = "added" if path not in a else "removed" if path not in b else "changed"
        changes.append({"path": path, "kind": kind, "old": a.get(path), "new": b.get(path),
                        "major": _is_major(doc_type, path)})
    return {"changes": changes, "major": any(c["major"] for c in changes), "count": len(changes)}
