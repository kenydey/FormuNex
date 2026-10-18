"""Load the bundled China regulatory data into the substance tables.

Sources (``app/resources/regulatory``, imported from MSDS-Generator):

* ``common_chemicals.json.gz`` — 131k substances (CAS, Chinese / English name, formula)
* ``hazard_chemical_info.json`` — 《危险化学品目录（2015 版）》, 2,857 entries with
  free-text GB 30000 classifications and 剧毒 / 易制毒 / 易制爆 remarks
* ``oel_gbz2_2019.json`` — GBZ 2.1-2019 occupational exposure limits
* ``hazard_statements.json`` / ``precaution_statements.json`` — H / P texts (Chinese)
* ``hpstatements_en.json`` — H / P texts (English), vendored mhchem/hpstatements CLP
  dataset: extracted from consolidated EU legal texts (Regulation (EC) No 1272/2008,
  EUR-Lex), CC BY 4.0, sample-verified against legislation.gov.uk Annex III/IV.
  Six MSDS-Generator hand-added entries are excluded (see that file's ``_formunex``).
  CLP does not adopt GHS categories 5 / Skin 3 / Eye 2B / Asp. Tox. 2 / Aquatic
  Acute 3 / Flam. Liq. 4, nor deleted P codes, so those seed codes keep
  ``text_en`` empty until a UN GHS source is provided.
* ``gb12268_2025.json`` — GB 12268 dangerous-goods entries (a 10-entry subset for now)

The run is idempotent: a release whose input digest matches an earlier one is
skipped unless forced, and re-running replaces only rows this importer owns
(``source='cn_catalog'`` classifications that nobody locked, the GBZ / GB 12268
rows, open review items it raised). Names are only filled where empty, so
edits made in the application survive a reseed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Connection, Engine

from ....db.models_regulatory import (
    GhsStatementRow,
    RegulatoryDataRelease,
    RegulatoryReviewItem,
    SubstanceClassificationRow,
    SubstanceGenericEntryRow,
    SubstanceOELRow,
    SubstanceRow,
    SubstanceTransportRow,
)
from . import cas as cas_mod
from .catalog_text_parser import ParseResult, parse_hazard_text
from .source_priority import SOURCE_CONFIDENCE

log = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).resolve().parents[3] / "resources" / "regulatory"
# Bump when parsing / mapping logic changes, so an unchanged data set is re-imported.
IMPORTER_VERSION = "3"  # 3: fill text_en from hpstatements_en.json (CLP English H/P)
OEL_STANDARD = "GBZ 2.1-2019"
TRANSPORT_STANDARD = "GB 12268-2025"
CATALOG_SOURCE_REF = "危险化学品目录（2015 版）序号 {no}"
SEED_REVIEW_KINDS = (
    "invalid_cas", "repaired_cas", "hazard_text", "duplicate_catalog_entry", "oel_unmatched",
    "transport_unlinked",
)
_FILES = (
    "common_chemicals.json.gz", "hazard_chemical_info.json", "oel_gbz2_2019.json",
    "hazard_statements.json", "precaution_statements.json", "gb12268_2025.json",
    "hpstatements_en.json",
)
_CHUNK = 5000


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _uid() -> str:
    return str(uuid.uuid4())


def _load_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(resources: Path, files: Iterable[str]) -> str:
    h = hashlib.sha256(IMPORTER_VERSION.encode())
    for name in files:
        p = resources / name
        if p.exists():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _chunks(rows: list[dict], size: int = _CHUNK):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _float(raw: Any) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
    return float(m.group()) if m and not re.search(r"[a-zA-Z]", str(raw)) else None


def _code(raw: str) -> str:
    """Normalise an H / P code; the source list has OCR slips like ``P2ll`` for P211
    (letters inside the three-digit number only — ``H350i`` stays as it is)."""
    code = re.sub(r"\s+", "", raw or "").upper()
    parts = []
    for seg in code.split("+"):
        body = seg[1:]
        if seg[:1] in ("H", "P") and len(body) == 3 and not body.isdigit() and set(body) <= set("0123456789LI"):
            seg = seg[0] + body.replace("L", "1").replace("I", "1")
        parts.append(seg)
    return "+".join(parts)


class _Review:
    def __init__(self, release_id: str) -> None:
        self.release_id = release_id
        self.items: list[dict] = []

    def add(self, kind: str, *, name: str = "", cas_no: str | None = None, **detail: Any) -> None:
        self.items.append({
            "id": _uid(), "kind": kind, "status": "open", "substance_id": None, "cas_no": cas_no,
            "name": (name or "")[:300], "detail": detail, "release_id": self.release_id, "created_at": _now(),
        })


def seed_regulatory_db(
    engine: Engine,
    *,
    resources: Path | None = None,
    label: str | None = None,
    include_common: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Import the bundled regulatory data. Returns run statistics."""
    resources = resources or RESOURCES_DIR
    files = [f for f in _FILES if include_common or f != "common_chemicals.json.gz"]
    digest = _digest(resources, files)
    with engine.begin() as conn:
        prior = conn.execute(
            select(RegulatoryDataRelease.id, RegulatoryDataRelease.stats).where(
                RegulatoryDataRelease.input_digest == digest
            )
        ).first()
        if prior and not force:
            return {"skipped": True, "release_id": prior.id, "stats": prior.stats}
        release_id = _uid()
        conn.execute(insert(RegulatoryDataRelease).values(
            id=release_id, label=label or f"seed-{_now():%Y%m%d-%H%M%S}", source="seed", stats={},
            input_digest=digest, created_at=_now(),
        ))
        review = _Review(release_id)
        stats: dict[str, Any] = {}
        subs = _collect_substances(resources, include_common, review, stats)
        cas_to_id = _upsert_substances(conn, subs, release_id, stats)
        _replace_catalog_classifications(conn, subs, cas_to_id, release_id, stats)
        _replace_generic_entries(conn, resources, release_id, stats)
        _replace_oel(conn, resources, cas_to_id, release_id, review, stats)
        _replace_transport(conn, resources, release_id, review, stats)
        _upsert_statements(conn, resources, stats)
        _replace_review_items(conn, review, stats)
        conn.execute(update(RegulatoryDataRelease).where(RegulatoryDataRelease.id == release_id).values(stats=stats))
    log.info("regulatory seed %s: %s", release_id, stats)
    return {"skipped": False, "release_id": release_id, "stats": stats}


# --------------------------------------------------------------------------- substances


def _collect_substances(
    resources: Path, include_common: bool, review: _Review, stats: dict
) -> dict[str, dict]:
    subs: dict[str, dict] = {}

    def entry(cas: str) -> dict:
        return subs.setdefault(cas, {
            "name_zh": "", "name_en": "", "mf": None, "aliases": [], "catalog_no": None,
            "highly_toxic": None, "precursor_control": None, "explosive_precursor": None,
            "classifications": {},
        })

    if include_common:
        data = _load_json(resources / "common_chemicals.json.gz")
        bad = 0
        for cas_raw, zh, en, mf in data["rows"]:
            cas, how = cas_mod.repair(cas_raw)
            if not cas:
                bad += 1
                review.add("invalid_cas", name=zh or en, cas_no=str(cas_raw)[:32], file="common_chemicals")
                continue
            e = entry(cas)
            e["name_zh"] = e["name_zh"] or zh
            e["name_en"] = e["name_en"] or en
            e["mf"] = e["mf"] or (mf or None)
        stats["common_rows"] = len(data["rows"])
        stats["common_invalid_cas"] = bad

    catalogue = _load_json(resources / "hazard_chemical_info.json")["chemicals"]
    repaired = generic = parsed_clean = multi = 0
    entries_by_cas: dict[str, list[int]] = {}
    for item in catalogue:
        name = re.sub(r"\s*\n\s*", "", item.get("chinese_name") or "")
        cas_list, invalid = catalog_cas_numbers(item.get("cas_number"))
        for original, cas, how in cas_list:
            if how in ("excel_date", "excel_serial"):
                repaired += 1
                review.add("repaired_cas", name=name, cas_no=cas, original=original, how=how,
                           catalog_no=item.get("index"))
        for original in invalid:
            review.add("invalid_cas", name=name, cas_no=str(original)[:32], file="hazard_chemical_info",
                       catalog_no=item.get("index"))
        if not cas_list:
            generic += 1
            continue
        if len(cas_list) > 1:
            multi += 1
        parsed = parse_hazard_text(item.get("hazard_category") or "")
        if not parsed.needs_review:
            parsed_clean += 1
        else:
            review.add("hazard_text", name=name, cas_no=cas_list[0][1], catalog_no=item.get("index"),
                       text=item.get("hazard_category"), reasons=parsed.review_reasons,
                       unparsed=parsed.unparsed)
        # One catalogue entry may list several CAS numbers (hydrates, isomers):
        # each is a substance carrying the entry's classification.
        for _original, cas, _how in cas_list:
            _apply_catalog_entry(entry(cas), item, name, parsed)
            entries_by_cas.setdefault(cas, []).append(item.get("index"))
    # The catalogue lists some substances more than once (e.g. 苯酚 as solid and as
    # solution, with different classifications). Their classifications are merged,
    # which is only right for one form — a person decides which apply.
    duplicates = {c: nos for c, nos in entries_by_cas.items() if len(nos) > 1}
    for c, nos in duplicates.items():
        review.add("duplicate_catalog_entry", name=subs[c]["name_zh"], cas_no=c, catalog_nos=nos)
    stats.update(catalog_duplicate_cas=len(duplicates))
    stats.update(catalog_entries=len(catalogue), catalog_cas_repaired=repaired, catalog_multi_cas=multi,
                 catalog_generic=generic, catalog_parsed_clean=parsed_clean)
    return subs


def catalog_cas_numbers(raw: str | None) -> tuple[list[tuple[str, str, str]], list[str]]:
    """``([(original, cas, how)], [invalid originals])`` for one catalogue CAS cell."""
    found: list[tuple[str, str, str]] = []
    invalid: list[str] = []
    for part in cas_mod.split_many(raw):
        cas, how = cas_mod.repair(part)
        if cas:
            if all(cas != c for _, c, _ in found):
                found.append((part, cas, how))
        else:
            invalid.append(part)
    return found, invalid


def _apply_catalog_entry(e: dict, item: dict, name: str, parsed: ParseResult) -> None:
    """Merge one catalogue entry (names, remarks, classifications) into substance record *e*."""
    e["name_zh"] = e["name_zh"] or name
    e["name_en"] = e["name_en"] or (item.get("english_name") or "")
    for alias in re.split(r"[;；]", item.get("alias") or ""):
        if alias.strip() and alias.strip() not in e["aliases"]:
            e["aliases"].append(alias.strip())
    if e["catalog_no"] is None:
        e["catalog_no"] = item.get("index")
    notes = item.get("notes") or ""
    if "剧毒" in notes:
        e["highly_toxic"] = True
    if "易制毒" in notes:
        e["precursor_control"] = True
    if "易制爆" in notes:
        e["explosive_precursor"] = True
    for p in parsed.items:
        key = (p.hazard_class, p.category)
        prev = e["classifications"].get(key)
        qualifier = p.qualifier
        if p.condition:
            qualifier = (qualifier + f"［条件：{p.condition}］").strip()
        if prev is None:
            e["classifications"][key] = {
                "h_code": p.h_code, "is_minimum": p.is_minimum, "qualifier": qualifier,
                "conditional": bool(p.condition), "catalog_no": item.get("index"),
            }
        elif qualifier and qualifier not in prev["qualifier"]:
            prev["qualifier"] = (prev["qualifier"] + qualifier)[:200]


def _upsert_substances(conn: Connection, subs: dict[str, dict], release_id: str, stats: dict) -> dict[str, str]:
    existing = {
        r.cas_no: r
        for r in conn.execute(select(SubstanceRow.id, SubstanceRow.cas_no, SubstanceRow.name_zh,
                                     SubstanceRow.name_en, SubstanceRow.mf).where(SubstanceRow.cas_no.isnot(None)))
    }
    now = _now()
    new_rows: list[dict] = []
    updates: list[dict] = []
    # Every substance already in the database is addressable (OEL rows may refer to ones an
    # earlier run created outside the bundled lists).
    cas_to_id: dict[str, str] = {c: r.id for c, r in existing.items()}
    for cas, e in subs.items():
        row = existing.get(cas)
        catalog_fields = {
            "hazardous_catalog_no": e["catalog_no"], "highly_toxic": e["highly_toxic"],
            "precursor_control": e["precursor_control"], "explosive_precursor": e["explosive_precursor"],
        }
        if row is None:
            sid = _uid()
            new_rows.append({
                "id": sid, "cas_no": cas, "ec_no": None, "name_zh": e["name_zh"][:300],
                "name_en": e["name_en"][:300], "aliases": e["aliases"], "mf": (e["mf"] or None),
                "smiles": None, "iecsc_listed": None, "key_supervised": None, "release_id": release_id,
                "created_at": now, "updated_at": now, **catalog_fields,
            })
            cas_to_id[cas] = sid
            continue
        cas_to_id[cas] = row.id
        patch: dict[str, Any] = {}
        if not row.name_zh and e["name_zh"]:
            patch["name_zh"] = e["name_zh"][:300]
        if not row.name_en and e["name_en"]:
            patch["name_en"] = e["name_en"][:300]
        if not row.mf and e["mf"]:
            patch["mf"] = e["mf"]
        if e["catalog_no"] is not None:
            patch.update(catalog_fields)
            patch["aliases"] = e["aliases"]
        if patch:
            updates.append({"_id": row.id, **patch})
    for chunk in _chunks(new_rows):
        conn.execute(insert(SubstanceRow), chunk)
    for u in updates:
        sid = u.pop("_id")
        conn.execute(update(SubstanceRow).where(SubstanceRow.id == sid).values(**u, updated_at=now))
    stats.update(substances_inserted=len(new_rows), substances_updated=len(updates),
                 substances_total=len(existing) + len(new_rows))
    return cas_to_id


def _replace_catalog_classifications(
    conn: Connection, subs: dict[str, dict], cas_to_id: dict[str, str], release_id: str, stats: dict
) -> None:
    conn.execute(delete(SubstanceClassificationRow).where(and_(
        SubstanceClassificationRow.source == "cn_catalog",
        SubstanceClassificationRow.locked_at.is_(None),
    )))
    kept = {
        (r.substance_id, r.hazard_class, r.category)
        for r in conn.execute(select(
            SubstanceClassificationRow.substance_id, SubstanceClassificationRow.hazard_class,
            SubstanceClassificationRow.category,
        ).where(SubstanceClassificationRow.source == "cn_catalog"))
    }
    now = _now()
    rows: list[dict] = []
    for cas, e in subs.items():
        sid = cas_to_id[cas]
        for (hazard_class, category), c in e["classifications"].items():
            if (sid, hazard_class, category) in kept:
                continue
            rows.append({
                "id": _uid(), "substance_id": sid, "hazard_class": hazard_class, "category": category,
                "h_code": c["h_code"], "is_minimum": c["is_minimum"], "qualifier": c["qualifier"][:200],
                "scl": None, "m_factor_acute": None, "m_factor_chronic": None, "ate_value": None,
                "ate_unit": None, "source": "cn_catalog",
                "source_ref": CATALOG_SOURCE_REF.format(no=c["catalog_no"]),
                # A conditional entry (depends on form / concentration) is only a hint.
                "confidence": 0.6 if c["conditional"] else SOURCE_CONFIDENCE["cn_catalog"],
                "locked_by": None, "locked_at": None, "release_id": release_id, "created_at": now,
            })
    for chunk in _chunks(rows):
        conn.execute(insert(SubstanceClassificationRow), chunk)
    stats["classifications_inserted"] = len(rows)


def _replace_generic_entries(conn: Connection, resources: Path, release_id: str, stats: dict) -> None:
    conn.execute(delete(SubstanceGenericEntryRow))
    rows = []
    for item in _load_json(resources / "hazard_chemical_info.json")["chemicals"]:
        if catalog_cas_numbers(item.get("cas_number"))[0]:
            continue
        parsed = parse_hazard_text(item.get("hazard_category") or "")
        rows.append({
            "id": _uid(), "catalog_no": item.get("index"), "name_zh": (item.get("chinese_name") or "")[:300],
            "name_en": (item.get("english_name") or "")[:300],
            "aliases": [a.strip() for a in re.split(r"[;；]", item.get("alias") or "") if a.strip()],
            "hazard_text": item.get("hazard_category") or "",
            "parsed": [p.__dict__ for p in parsed.items],
            # Matching a formulation component to a generic entry ("××化合物") needs
            # chemistry, not string equality; it is always confirmed by a person.
            "match_rule": {"by": "name", "requires_review": True},
            "release_id": release_id,
        })
    for chunk in _chunks(rows):
        conn.execute(insert(SubstanceGenericEntryRow), chunk)
    stats["generic_entries"] = len(rows)


def _ensure_substance(conn: Connection, cas: str, name_zh: str, name_en: str,
                      cas_to_id: dict[str, str], release_id: str) -> str:
    sid = cas_to_id.get(cas)
    if sid:
        return sid
    sid = _uid()
    now = _now()
    conn.execute(insert(SubstanceRow).values(
        id=sid, cas_no=cas, name_zh=name_zh[:300], name_en=name_en[:300], aliases=[], release_id=release_id,
        created_at=now, updated_at=now,
    ))
    cas_to_id[cas] = sid
    return sid


def _replace_oel(conn: Connection, resources: Path, cas_to_id: dict[str, str], release_id: str,
                 review: _Review, stats: dict) -> None:
    conn.execute(delete(SubstanceOELRow).where(SubstanceOELRow.standard == OEL_STANDARD))
    seen: set[str] = set()
    rows, unmatched = [], 0
    for rec in _load_json(resources / "oel_gbz2_2019.json")["records"]:
        cas_list = [c for c, _ in (cas_mod.repair(x) for x in cas_mod.split_many(rec.get("cas_number"))) if c]
        if not cas_list:
            unmatched += 1
            review.add("oel_unmatched", name=rec.get("name_cn") or "", cas_no=None,
                       raw_cas=rec.get("cas_number"), record=rec)
            continue
        remark = (rec.get("remark") or "").strip()
        for cas in cas_list:
            sid = _ensure_substance(conn, cas, rec.get("name_cn") or "", rec.get("name_en") or "",
                                    cas_to_id, release_id)
            if sid in seen:
                continue
            seen.add(sid)
            rows.append({
                "id": _uid(), "substance_id": sid, "standard": OEL_STANDARD,
                "mac": _float(rec.get("mac_mg_m3")), "pc_twa": _float(rec.get("pc_twa_mg_m3")),
                "pc_stel": _float(rec.get("pc_stel_mg_m3")),
                "critical_effect": (rec.get("critical_effect") or "")[:300],
                "notes": "" if remark in ("―", "-", "—") else remark[:200], "release_id": release_id,
            })
    for chunk in _chunks(rows):
        conn.execute(insert(SubstanceOELRow), chunk)
    stats.update(oel_rows=len(rows), oel_unmatched=unmatched)


def _replace_transport(conn: Connection, resources: Path, release_id: str, review: _Review, stats: dict) -> None:
    conn.execute(delete(SubstanceTransportRow).where(SubstanceTransportRow.standard == TRANSPORT_STANDARD))
    data = _load_json(resources / "gb12268_2025.json")
    entries = data.get("entries") or {}
    names = [v.get("name_cn") for v in entries.values() if v.get("name_cn")]
    by_name: dict[str, list[str]] = {}
    if names:
        for r in conn.execute(select(SubstanceRow.id, SubstanceRow.name_zh).where(SubstanceRow.name_zh.in_(names))):
            by_name.setdefault(r.name_zh, []).append(r.id)
    rows, unlinked = [], 0
    for un, v in entries.items():
        ids = by_name.get(v.get("name_cn") or "", [])
        sid = ids[0] if len(ids) == 1 else None
        if sid is None:
            unlinked += 1
            review.add("transport_unlinked", name=v.get("name_cn") or "", un_number=un, candidates=len(ids))
        rows.append({
            "id": _uid(), "un_number": str(un), "substance_id": sid,
            "proper_shipping_name_zh": v.get("name_cn") or "", "proper_shipping_name_en": v.get("name_en") or "",
            "transport_class": str(v.get("class") or ""), "subsidiary_risk": str(v.get("subsidiary") or ""),
            "packing_group": v.get("pg") or None, "marine_pollutant": v.get("marine_pollutant"),
            "special_provisions": "", "standard": TRANSPORT_STANDARD, "release_id": release_id,
        })
    if rows:
        conn.execute(insert(SubstanceTransportRow), rows)
    stats.update(transport_rows=len(rows), transport_unlinked=unlinked)


def _upsert_statements(conn: Connection, resources: Path, stats: dict) -> None:
    existing = {r.code: r for r in conn.execute(select(
        GhsStatementRow.code, GhsStatementRow.text_zh, GhsStatementRow.text_en))}
    fresh: dict[str, dict] = {}
    for kind, fname in (("H", "hazard_statements.json"), ("P", "precaution_statements.json")):
        for s in _load_json(resources / fname)["statements"]:
            code = _code(s.get("code") or "")
            if not code or code in fresh:
                continue
            fresh[code] = {
                "code": code, "kind": kind, "text_zh": re.sub(r"\s*\n\s*", "", s.get("statement") or ""),
                "text_en": "", "hazard_type": s.get("hazard_type") or "",
                "hazard_category": s.get("hazard_category") or "",
                "precaution_category": s.get("precaution_category") or "",
                "usage_condition": s.get("usage_condition") or "",
            }
    # English texts: mhchem CLP extraction, keyed by the same normalised code.
    # The file is optional (fixture resource sets omit it) — without it text_en stays "".
    en_map: dict[str, str] = {}
    en_path = resources / "hpstatements_en.json"
    if en_path.exists():
        raw = _load_json(en_path).get("statements", {})
        for key, text in raw.items():
            en_map[_code(key.split("/")[-1])] = re.sub(r"\s+", " ", str(text or "")).strip()
    for v in fresh.values():
        v["text_en"] = en_map.get(v["code"], "")
    new = [v for k, v in fresh.items() if k not in existing]
    if new:
        conn.execute(insert(GhsStatementRow), new)
    filled = filled_en = 0
    for code, v in fresh.items():
        row = existing.get(code)
        if row is None:
            continue
        if not row.text_zh and v["text_zh"]:
            conn.execute(update(GhsStatementRow).where(GhsStatementRow.code == code).values(text_zh=v["text_zh"]))
            filled += 1
        # Never overwrite a human-edited English text; only fill blanks.
        if not (row.text_en or "") and v["text_en"]:
            conn.execute(update(GhsStatementRow).where(GhsStatementRow.code == code).values(text_en=v["text_en"]))
            filled_en += 1
    stats.update(statements_inserted=len(new), statements_filled=filled,
                 statements_en_filled=filled_en)


def _replace_review_items(conn: Connection, review: _Review, stats: dict) -> None:
    conn.execute(delete(RegulatoryReviewItem).where(and_(
        RegulatoryReviewItem.status == "open", RegulatoryReviewItem.kind.in_(SEED_REVIEW_KINDS),
    )))
    settled = {
        (r.kind, r.cas_no or "", r.name)
        for r in conn.execute(select(RegulatoryReviewItem.kind, RegulatoryReviewItem.cas_no,
                                     RegulatoryReviewItem.name).where(RegulatoryReviewItem.status != "open"))
    }
    rows = [i for i in review.items if (i["kind"], i["cas_no"] or "", i["name"]) not in settled]
    for chunk in _chunks(rows):
        conn.execute(insert(RegulatoryReviewItem), chunk)
    by_kind: dict[str, int] = {}
    for i in rows:
        by_kind[i["kind"]] = by_kind.get(i["kind"], 0) + 1
    stats["review_items"] = by_kind
