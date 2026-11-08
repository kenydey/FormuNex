"""Chemical name / CAS lookup: catalog → local 131k substance library (DB) →
PubChem PUG REST → PubChemPy → chemtools PubChem → SureChEMBL, with 24h cache
for hits and a 5-minute cache for misses.

The local-library tier queries the seeded ``substances`` table (``common_chemicals``,
131k rows) by exact CAS / exact or case-insensitive Chinese / English name, so
formulation / material auto-matching resolves locally without a PubChem round-trip.
It runs before every network tier; PubChem is only reached on a local miss. The
local library carries CAS + names + formula only — SMILES / molar mass stay
``None`` there rather than being filled from somewhere else, and every hit is
labelled with its true ``source`` (``local_library`` vs ``pubchem`` vs ...).
"""
from __future__ import annotations

import logging
import time
from .errors import degrade_return
import re
from typing import Any
from urllib.parse import quote

from sqlalchemy import cast, func, select, String

from ..domain.knowledge import RAW_MATERIALS

from .chem_common import cache_put
from .http_safe import make_client

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[一-鿿]")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SEC = 86400
# P2-C18: a miss is weak evidence (the library / network may gain the
# entry tomorrow); negative results live 5 minutes, positives a day.
_NEGATIVE_TTL_SEC = 300


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    ttl = _NEGATIVE_TTL_SEC if payload.get("found") is False else _TTL_SEC
    if time.time() - ts > ttl:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return cache_put(_CACHE, key, payload)


def _zh_from_query(q: str) -> str:
    parts = _CJK_RE.findall(q)
    return "".join(parts) if parts else ""


def _lookup_catalog(q: str) -> dict[str, Any] | None:
    key = q.strip()
    spec = RAW_MATERIALS.get(key)
    if not spec:
        for name, row in RAW_MATERIALS.items():
            if name.lower() == key.lower():
                spec = row
                key = name
                break
    if not spec:
        return None
    return {
        "query": q,
        "cas": spec.get("cas_no") or "",
        "iupac_name": key,
        "zh_name": spec.get("zh_name") or _zh_from_query(q),
        "formula": spec.get("formula") or "",
        "smiles": spec.get("smiles"),
        "molar_mass": spec.get("molar_mass"),
        "found": True,
        "source": "catalog",
    }


def _row_to_local_hit(q: str, row) -> dict[str, Any]:
    # The library carries CAS + names + formula only: SMILES / molar mass
    # are genuinely absent, so they stay None — never backfilled from
    # another source under this tier's label.
    return {
        "query": q,
        "cas": row.cas_no or "",
        "iupac_name": row.name_en or q,
        "zh_name": row.name_zh or _zh_from_query(q),
        "formula": row.mf or "",
        "smiles": row.smiles,
        "molar_mass": None,
        "found": True,
        "source": "local_library",
    }


def _alias_condition(query: str):
    """Aliases JSON-contains, in a form that compiles on SQLite and PG.

    No json_each / jsonb_each split: the aliases column serialises as a
    JSON array on both dialects, so a quoted-element LIKE on the cast
    text matches an exact array element on either. Two needles cover
    the serialisation split: raw text (PG jsonb output) and the
    ``\\uXXXX``-escaped form (SQLAlchemy's default JSON serialiser on
    SQLite stores non-ASCII that way). LIKE metacharacters in the query
    are escaped; a quote inside the query simply misses (fail-open to
    the next tier), never false-positives.
    """
    import json

    from sqlalchemy import or_

    from ..db.models_regulatory import SubstanceRow

    def needle(text: str) -> str:
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    needles = {needle(query)}
    needles.add(needle(json.dumps(query, ensure_ascii=True)[1:-1]))
    col = cast(SubstanceRow.aliases, String)
    return or_(*[col.like(f'%"{n}"%', escape="\\") for n in sorted(needles)])


def _lookup_local_library(q: str) -> dict[str, Any] | None:
    """Tier 2: the seeded 131k local substance library (``substances`` table).

    P2-C17: match tiers run in priority order — exact CAS, exact Chinese
    name, exact English name, case-insensitive names, aliases — and the
    first tier with a hit wins. Within one tier, two or more rows is
    ambiguity, not an arbitrary ``limit(1)`` pick: the chain stops with
    an explicit ambiguous marker. Every tier except aliases is
    index-backed: ``cas_no`` (unique), ``name_zh`` / ``name_en`` (plain
    indexes) and ``lower(name_zh)`` / ``lower(name_en)`` (expression
    indexes, alembic 0065). A short-lived session from the process-wide
    factory keeps the ``lookup_chemical(q)`` signature unchanged; any DB
    problem degrades to ``None`` so the chain falls through to PubChem
    exactly as before.
    """
    from ..db.database import default_session_factory
    from ..db.models_regulatory import SubstanceRow
    from .plm.regulatory.cas import repair as repair_cas

    query = (q or "").strip()
    if not query:
        return None
    try:
        cas, _how = repair_cas(query)
        lowered = query.lower()
        tiers: list[tuple[str, Any]] = []
        if cas:
            tiers.append(("cas", SubstanceRow.cas_no == cas))
        tiers.append(("name_zh", SubstanceRow.name_zh == query))
        tiers.append(("name_en", SubstanceRow.name_en == query))
        tiers.append(("name_zh_ci", func.lower(SubstanceRow.name_zh) == lowered))
        tiers.append(("name_en_ci", func.lower(SubstanceRow.name_en) == lowered))
        tiers.append(("alias", _alias_condition(query)))
        with default_session_factory()() as session:
            for tier_name, cond in tiers:
                rows = session.execute(
                    select(SubstanceRow).where(cond).order_by(SubstanceRow.id).limit(2)
                ).scalars().all()
                if len(rows) == 1:
                    return _row_to_local_hit(q, rows[0])
                if len(rows) > 1:
                    return {
                        "query": q,
                        "cas": "",
                        "iupac_name": "",
                        "zh_name": "",
                        "formula": "",
                        "smiles": None,
                        "molar_mass": None,
                        "found": False,
                        "ambiguous": True,
                        "ambiguous_tier": tier_name,
                        "candidates": [
                            {"cas": r.cas_no or "", "name_zh": r.name_zh or "", "name_en": r.name_en or ""}
                            for r in rows
                        ],
                        "source": "local_library",
                    }
        return None
    except Exception as exc:
        return degrade_return(logger, exc, "local library lookup failed", None)


def _lookup_pubchem(q: str) -> dict[str, Any] | None:
    encoded = quote(q.strip(), safe="")
    props_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
        "/property/MolecularFormula,MolecularWeight,IUPACName/JSON"
    )
    cas_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
        "/xrefs/RegistryNumber/JSON"
    )
    try:
        with make_client(timeout=12.0) as client:
            props_resp = client.get(props_url)
            if props_resp.status_code != 200:
                return None
            props_data = props_resp.json()
            props = (props_data.get("PropertyTable") or {}).get("Properties") or []
            row = props[0] if props else {}
            # P2-C16: cross-check by CID. The name query can match several
            # compounds; the xrefs response is for the name too, so its
            # first entry may belong to a DIFFERENT compound than the
            # props row. Only accept RegistryNumbers from the entry whose
            # CID equals the props row's CID; on mismatch no CAS is
            # adopted (a wrong CAS is worse than a missing one).
            cid = row.get("CID")
            cas = ""
            cas_note = ""
            cas_resp = client.get(cas_url)
            if cas_resp.status_code == 200:
                cas_data = cas_resp.json()
                info_list = (cas_data.get("InformationList") or {}).get("Information") or []
                if cid is not None:
                    info = next((i for i in info_list if i.get("CID") == cid), None)
                    if info is None:
                        cas_note = "RegistryNumber 的 CID 与属性 CID 不一致，未采纳"
                        info = {}
                else:
                    info = info_list[0] if info_list else {}
                nums = info.get("RegistryNumber", []) or []
                from .plm.regulatory.cas import is_valid as cas_is_valid

                valid = [str(n) for n in nums if cas_is_valid(str(n))]
                if len(valid) > 1:
                    return {
                        "query": q,
                        "cas": "",
                        "iupac_name": row.get("IUPACName") or q,
                        "zh_name": _zh_from_query(q),
                        "formula": row.get("MolecularFormula") or "",
                        "smiles": None,
                        "molar_mass": row.get("MolecularWeight"),
                        "found": False,
                        "ambiguous": True,
                        "candidates": valid,
                        "source": "pubchem",
                    }
                cas = valid[0] if valid else ""
            # Tier 2: harvest structured supplier list from PubChem PUG-View
            # ``Chemical Vendors`` category.
            suppliers = _lookup_pubchem_vendors(client, encoded)
            hit = {
                "query": q,
                "cas": cas,
                "iupac_name": row.get("IUPACName") or q,
                "zh_name": _zh_from_query(q),
                "formula": row.get("MolecularFormula") or "",
                "smiles": None,
                "molar_mass": row.get("MolecularWeight"),
                "found": True,
                "source": "pubchem",
                "suppliers": suppliers,
            }
            if cas_note:
                hit["cas_note"] = cas_note
            return hit
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def _lookup_pubchem_vendors(client, encoded: str) -> list[dict[str, Any]]:
    """Fetch the ``Chemical Vendors`` list for a PubChem compound name.

    Returns a list of ``Supplier``-shaped dicts (name, url, product_url).
    Only fields PubChem actually exposes are emitted: price / stock /
    delivery are deliberately absent rather than emitted as ``null``
    placeholders -- PubChem's vendor category carries no commercial terms,
    so a placeholder column would only ever render as "-".

    Note: PubChem does not expose vendor country either, so no ``country``
    field is emitted.
    """
    try:
        cid_url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
            "/cids/JSON"
        )
        cid_resp = client.get(cid_url)
        if cid_resp.status_code != 200:
            return []
        cid_data = cid_resp.json()
        cids = (cid_data.get("IdentifierList") or {}).get("CID") or []
        if not cids:
            return []
        cid = cids[0]
        vendors_url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/categories/compound/{cid}/JSON"
        )
        vendors_resp = client.get(vendors_url, timeout=8.0)
        if vendors_resp.status_code != 200:
            return []
        categories = (vendors_resp.json().get("SourceCategories") or {}).get("Categories") or []
        for cat in categories:
            if cat.get("Category") != "Chemical Vendors":
                continue
            out: list[dict[str, Any]] = []
            for src in cat.get("Sources") or []:
                name = (src.get("SourceName") or "").strip()
                if not name:
                    continue
                supplier = {
                    "name": name,
                    "url": src.get("SourceURL"),
                    "product_url": src.get("SourceRecordURL"),
                }
                out.append(supplier)
            return out
        return []
    except Exception as exc:
        degrade_return(logger, exc, "pubchem vendors fetch failed", [])
        return []


def _lookup_compound_synonyms(q: str) -> dict[str, Any] | None:
    """PubChemPy compound lookup with synonym zh_name / CAS / SMILES (PR-2C)."""
    from .compounds import lookup_compound

    data = lookup_compound(q.strip())
    if not data:
        return None
    if not any(data.get(k) for k in ("cas", "smiles", "zh_name", "formula", "molar_mass")):
        return None
    return {
        "query": q,
        "cas": data.get("cas") or "",
        "iupac_name": data.get("iupac_name") or q,
        "zh_name": data.get("zh_name") or _zh_from_query(q),
        "formula": data.get("formula") or "",
        "smiles": data.get("smiles"),
        "molar_mass": data.get("molar_mass"),
        "found": True,
        "source": "pubchempy_compound",
    }


def _lookup_offline_compounds(q: str) -> dict[str, Any] | None:
    from .compounds import lookup

    data = lookup(q)
    if not data:
        return None
    return {
        "query": q,
        "cas": "",
        "iupac_name": q,
        "zh_name": _zh_from_query(q),
        "formula": "",
        "smiles": data.get("smiles"),
        "molar_mass": data.get("molar_mass"),
        "found": True,
        "source": "pubchempy",
    }


def _lookup_chemtools(q: str) -> dict[str, Any] | None:
    """Tier 4: native PubChem name→SMILES/CAS gap-fill — reached only when
    catalog, PubChem REST and PubChemPy all miss (rare; same data source as
    the earlier PubChem tiers but a different query shape)."""
    from . import chemtools

    if not chemtools.gateway_enabled():
        return None
    smiles = chemtools.name_to_smiles(q)
    cas = chemtools.name_to_cas(q)
    if not smiles and not cas:
        return None
    return {
        "query": q,
        "cas": cas or "",
        "iupac_name": q,
        "zh_name": _zh_from_query(q),
        "formula": "",
        "smiles": smiles,
        "molar_mass": None,
        "found": True,
        "source": "pubchem",
    }


def _lookup_surechembl(q: str) -> dict[str, Any] | None:
    """Tier last: SureChEMBL official API name/SMILES identification."""
    try:
        from .surechembl_lookup import lookup_surechembl

        return lookup_surechembl(q)
    except Exception as exc:
        return degrade_return(logger, exc, "surechembl lookup failed", None)


def lookup_chemical(q: str) -> dict[str, Any]:
    """Resolve a chemical query (CN/EN name or CAS) to structured metadata."""
    key = (q or "").strip().lower()
    cache_key = f"{key}|"
    if not key:
        return {
            "query": q,
            "cas": "",
            "iupac_name": "",
            "zh_name": "",
            "formula": "",
            "smiles": None,
            "molar_mass": None,
            "found": False,
            "source": "empty",
            "providers_tried": [],
            "suppliers": [],
        }
    cached = _cache_get(cache_key)
    if cached:
        return cached

    providers_tried: list[str] = []
    resolvers = (
        ("catalog", _lookup_catalog),
        ("local_library", _lookup_local_library),
        ("pubchem", _lookup_pubchem),
        ("pubchempy_compound", _lookup_compound_synonyms),
        ("pubchempy", _lookup_offline_compounds),
        ("pubchem", _lookup_chemtools),
        ("surechembl", _lookup_surechembl),
    )
    for provider_name, resolver in resolvers:
        # Avoid double-counting identical provider labels in tried list.
        if provider_name not in providers_tried:
            providers_tried.append(provider_name)
        hit = resolver(q.strip())
        if hit:
            hit = dict(hit)
            hit["providers_tried"] = list(providers_tried)
            return _cache_put(cache_key, hit)
    empty = {
        "query": q,
        "cas": "",
        "iupac_name": q.strip(),
        "zh_name": _zh_from_query(q),
        "formula": "",
        "smiles": None,
        "molar_mass": None,
        "found": False,
        "source": "none",
        "providers_tried": providers_tried,
        "suppliers": [],
    }
    return _cache_put(cache_key, empty)
