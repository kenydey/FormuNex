"""Chemical name / CAS lookup: catalog → local 131k substance library (DB) →
PubChem PUG REST → PubChemPy → chemtools PubChem → SureChEMBL, with 24h cache.

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
from .errors import degrade_return
import re
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, or_, select

from ..domain.knowledge import RAW_MATERIALS

from .chem_common import cache_get, cache_put
from .http_safe import make_client

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[一-鿿]")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SEC = 86400


def _cache_get(key: str) -> dict[str, Any] | None:
    return cache_get(_CACHE, key, _TTL_SEC)


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


def _lookup_local_library(q: str) -> dict[str, Any] | None:
    """Tier 2: the seeded 131k local substance library (``substances`` table).

    Exact CAS (normalised with the seed's ``cas.repair`` — including the Excel
    date / serial corruption fix, checksum-verified) or exact / case-insensitive
    Chinese / English name. Every branch is index-backed: ``cas_no`` (unique),
    ``name_zh`` / ``name_en`` (plain indexes) and ``lower(name_zh)`` /
    ``lower(name_en)`` (expression indexes, alembic 0065). A short-lived session
    from the process-wide factory keeps the ``lookup_chemical(q)`` signature
    unchanged; any DB problem degrades to ``None`` so the chain falls through
    to PubChem exactly as before.
    """
    from ..db.database import default_session_factory
    from ..db.models_regulatory import SubstanceRow
    from .plm.regulatory.cas import repair as repair_cas

    query = (q or "").strip()
    if not query:
        return None
    try:
        cas, _how = repair_cas(query)
        conditions = []
        if cas:
            conditions.append(SubstanceRow.cas_no == cas)
        conditions.append(SubstanceRow.name_zh == query)
        conditions.append(SubstanceRow.name_en == query)
        lowered = query.lower()
        conditions.append(func.lower(SubstanceRow.name_zh) == lowered)
        conditions.append(func.lower(SubstanceRow.name_en) == lowered)
        with default_session_factory()() as session:
            row = session.execute(
                select(SubstanceRow).where(or_(*conditions)).order_by(SubstanceRow.id).limit(1)
            ).scalar_one_or_none()
        if row is None:
            return None
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
            cas = ""
            cas_resp = client.get(cas_url)
            if cas_resp.status_code == 200:
                cas_data = cas_resp.json()
                info_list = (cas_data.get("InformationList") or {}).get("Information") or [{}]
                nums = info_list[0].get("RegistryNumber", [])
                if nums:
                    cas = str(nums[0])
            # Tier 2: harvest structured supplier list from PubChem PUG-View
            # ``Chemical Vendors`` category.
            suppliers = _lookup_pubchem_vendors(client, encoded)
            return {
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
