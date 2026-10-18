"""PubChem GHS classification enrichment (补齐计划 1-6).

A Celery task (``formunex.pubchem_enrich``) resolves a substance to a PubChem
CID, fetches its GHS classification from PUG-View, and writes the result
into ``substance_classifications`` with ``source='pubchem'`` and confidence
0.6 (see ``source_priority.SOURCE_CONFIDENCE``).

Safety rules:

* **Fetch first, write after.** The whole PUG-View payload is parsed before
  any row is touched, so a failed fetch never leaves partial data.
* **Replace semantics per source.** A successful fetch replaces this
  substance's unlocked ``pubchem`` rows. Locked rows (EHS-confirmed) and
  every other source are never touched.
* **Never wipe on an empty fetch.** When PubChem has no GHS section, or every
  class name is unmapped, the existing ``pubchem`` rows are kept and the
  run reports ``no_data`` / ``unmapped`` instead of deleting data on the
  basis of a parse gap.
* **Retryable vs terminal.** Transport errors and PubChem 5xx raise
  :class:`PubChemUnavailable` (the Celery task retries); 404 / "no GHS
  section" raise :class:`PubChemNotFound` (a definitive answer, no retry).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

import httpx

from ....db.models_regulatory import SubstanceClassificationRow, SubstanceRow
from ...http_safe import make_client
from .hazard_classes import HAZARD_CLASSES, english_class_to_code

SOURCE = "pubchem"
CONFIDENCE = 0.6
_PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_PUG_VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound"

_STATEMENT_RE = re.compile(
    # PubChem statement prefixes carry footnote markers: "H370 **: ...",
    # "H301 (99.9%): ..." (notifier report frequency).
    r"^(?P<h>H\d{3})(?:\s*(?:\*\*|\([^)]*\)))*\s*:\s*(?:.*?)\s*\[(?:Danger|Warning|Caution)\s+(?P<cls>.*?)\]\s*$",
    re.IGNORECASE,
)


_STOT_SE3_H = {"H335": "（呼吸道刺激）", "H336": "（麻醉效应）"}


def _build_h_code_map() -> dict[str, list[tuple[str, str]]]:
    """Invert the GB 30000 H-code table: H code -> [(hazard_class, category)]."""
    out: dict[str, list[tuple[str, str]]] = {}
    for hc in HAZARD_CLASSES:
        for cat, h in hc.h_codes.items():
            if h:
                out.setdefault(h, []).append((hc.code, cat))
    return out


_H_CODE_MAP = _build_h_code_map()


def _severity(category: str) -> float:
    """Lower = more severe. Used to resolve an H code spanning categories."""
    m = re.match(r"(\d+(?:\.\d+)?)", category)
    if m:
        return float(m.group(1))
    if category == "A":
        return 0.0
    if category == "B":
        return 1.0
    if category in ("unstable", "gas", "refrigerated"):
        return 0.0
    if category == "lact":
        return 1.5
    return 99.0


class PubChemUnavailable(RuntimeError):
    """Transient: transport error or PubChem 5xx. The task retries."""


class PubChemNotFound(RuntimeError):
    """Terminal: no CID, or no GHS section for this compound. No retry."""


@dataclass
class EnrichedClassification:
    hazard_class: str
    category: str
    h_code: str | None = None
    qualifier: str = ""


@dataclass
class FetchResult:
    cid: int
    classifications: list[EnrichedClassification] = field(default_factory=list)
    h_codes: list[str] = field(default_factory=list)
    p_codes: list[str] = field(default_factory=list)
    signal: str = ""
    unmapped: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    source_ref: str = ""


def _sections(node) -> list[dict]:
    if isinstance(node, dict):
        return node.get("Section") or []
    return []


def _walk_sections(node):
    """Yield every section dict in a PUG-View record, depth-first."""
    for sec in _sections(node):
        yield sec
        yield from _walk_sections(sec)


def _string_values(info: dict) -> list[str]:
    value = info.get("Value") or {}
    out: list[str] = []
    for item in value.get("StringWithMarkup") or []:
        if isinstance(item, dict) and item.get("String"):
            out.append(str(item["String"]))
    if value.get("String"):
        out.append(str(value["String"]))
    return out


def _parse_ghs_section(ghs_section: dict) -> FetchResult:
    """Parse the PUG-View ``GHS Classification`` section.

    Real PUG-View shape (verified against the live API, methanol CID 887):
    the section carries a *flat* ``Information`` list of repeated notifier
    blocks — Pictogram(s), Signal, GHS Hazard Statements, Precautionary
    Statement Codes, then the next notifier's block. There is no
    "GHS Hazard Classes" subsection and no explicit category: each hazard
    statement reads ``H225: ... [Danger Flammable liquids]``.

    Only the *first* block is parsed — it is the primary / harmonised
    classification; later blocks are individual notifier submissions that
    routinely disagree (H301 vs H302 for the same class). The category is
    recovered from the H code via the GB 30000 H-code table; an H code that
    covers several categories of one class (e.g. H300 → oral 1 / 2) takes
    the most severe category and is flagged in ``review_reasons``.
    """
    infos = ghs_section.get("Information") or []
    # First block = entries up to the second "Pictogram(s)".
    first_block: list[dict] = []
    pictograms = 0
    for info in infos:
        if info.get("Name") == "Pictogram(s)":
            pictograms += 1
            if pictograms > 1:
                break
        first_block.append(info)

    h_codes: list[str] = []
    p_codes: list[str] = []
    signal = ""
    review_reasons: list[str] = []
    unmapped: list[str] = []
    classifications: list[EnrichedClassification] = []
    seen: set[tuple[str, str]] = set()

    for info in first_block:
        name = str(info.get("Name") or "")
        values = _string_values(info)
        if name == "Signal" and values:
            signal = values[0]
        elif name == "Precautionary Statement Codes":
            for value in values:
                for code in re.findall(r"P\d{3}", value):
                    if code not in p_codes:
                        p_codes.append(code)
        elif name == "GHS Hazard Statements":
            for value in values:
                m = _STATEMENT_RE.match(value.strip())
                if not m:
                    unmapped.append(value)
                    continue
                h_code = m.group("h")
                bracket_class = (m.group("cls") or "").strip()
                if h_code not in h_codes:
                    h_codes.append(h_code)
                code = english_class_to_code(bracket_class) if bracket_class else None
                # STOT SE 3 has no single H code in the GB 30000 table
                # (H335 = respiratory irritation, H336 = narcotic effects),
                # so it never appears in _H_CODE_MAP — handle it directly.
                if h_code in _STOT_SE3_H and code == "stot_se":
                    key = ("stot_se", "3")
                    if key not in seen:
                        seen.add(key)
                        classifications.append(EnrichedClassification(
                            hazard_class="stot_se", category="3",
                            h_code=h_code, qualifier=_STOT_SE3_H[h_code],
                        ))
                    continue
                candidates = _H_CODE_MAP.get(h_code, [])
                if code is not None:
                    candidates = [c for c in candidates if c[0] == code]
                    if not candidates and bracket_class not in unmapped:
                        unmapped.append(f"{h_code} [{bracket_class}]")
                        continue
                if not candidates:
                    if bracket_class not in unmapped:
                        unmapped.append(f"{h_code} [{bracket_class}]")
                    continue
                classes = {c[0] for c in candidates}
                if len(classes) > 1:
                    # e.g. H241: self-reactive type D and organic-peroxide
                    # type D. Without a bracket class we cannot attribute it.
                    unmapped.append(f"{h_code} (ambiguous across classes)")
                    continue
                if len(candidates) > 1:
                    cats = sorted({c[1] for c in candidates})
                    review_reasons.append(
                        f"{h_code} 无法区分 {candidates[0][0]} 类别 {'/'.join(cats)}，"
                        "取较严类别，需人工复核"
                    )
                chosen = min(candidates, key=lambda c: (_severity(c[1]), c[1]))
                key = (chosen[0], chosen[1])
                if key in seen:
                    continue
                seen.add(key)
                classifications.append(EnrichedClassification(
                    hazard_class=chosen[0], category=chosen[1],
                    h_code=h_code, qualifier="",
                ))
    return FetchResult(
        cid=0, classifications=classifications, h_codes=h_codes, p_codes=p_codes,
        signal=signal, unmapped=unmapped, review_reasons=review_reasons,
    )


def fetch_pubchem_ghs(query: str, *, client: httpx.Client | None = None) -> FetchResult:
    """Fetch and parse the GHS classification for a name or CAS number.

    ``query`` is a PubChem compound name or a CAS RN (PubChem resolves
    registry numbers as names). Raises :class:`PubChemUnavailable` on
    transport errors / PubChem 5xx, :class:`PubChemNotFound` when the
    compound or its GHS section does not exist.
    """
    q = (query or "").strip()
    if not q:
        raise PubChemNotFound("empty query")
    own_client = client is None
    client = client or make_client(timeout=20.0)
    try:
        try:
            cid_resp = client.get(f"{_PUG}/compound/name/{q}/cids/JSON")
        except httpx.HTTPError as exc:
            raise PubChemUnavailable(f"PubChem CID lookup failed: {exc}") from exc
        if cid_resp.status_code == 404:
            raise PubChemNotFound(f"PubChem has no compound for {q!r}")
        if cid_resp.status_code != 200:
            raise PubChemUnavailable(f"PubChem CID lookup HTTP {cid_resp.status_code}")
        try:
            cids = (cid_resp.json().get("IdentifierList") or {}).get("CID") or []
        except ValueError as exc:
            raise PubChemUnavailable(f"PubChem CID lookup returned bad JSON: {exc}") from exc
        if not cids:
            raise PubChemNotFound(f"PubChem has no compound for {q!r}")
        cid = int(cids[0])

        url = f"{_PUG_VIEW}/{cid}/JSON/?heading=GHS+Classification"
        try:
            view_resp = client.get(url)
        except httpx.HTTPError as exc:
            raise PubChemUnavailable(f"PubChem PUG-View fetch failed: {exc}") from exc
        if view_resp.status_code == 404:
            raise PubChemNotFound(f"PubChem has no GHS classification for CID {cid}")
        if view_resp.status_code != 200:
            raise PubChemUnavailable(f"PubChem PUG-View HTTP {view_resp.status_code}")
        try:
            record = view_resp.json().get("Record") or {}
        except ValueError as exc:
            raise PubChemUnavailable(f"PubChem PUG-View returned bad JSON: {exc}") from exc
        ghs_section = next(
            (s for s in _walk_sections(record) if s.get("TOCHeading") == "GHS Classification"),
            None,
        )
        if ghs_section is None:
            raise PubChemNotFound(f"PubChem has no GHS classification for CID {cid}")
        result = _parse_ghs_section(ghs_section)
        result.cid = cid
        result.source_ref = url
        return result
    finally:
        if own_client:
            client.close()


def apply_pubchem_enrichment(session, substance: SubstanceRow, result: FetchResult) -> dict:
    """Replace a substance's unlocked ``pubchem`` rows with a fetch result.

    No-op (keeps existing rows) when the fetch carried no classifications —
    an empty fetch must never delete data. Flushes only; the caller commits.
    """
    if not result.classifications:
        return {
            "substance_id": substance.id,
            "status": "no_data" if not result.unmapped else "unmapped",
            "written": 0,
            "replaced": 0,
            "unmapped": result.unmapped,
            "review_reasons": result.review_reasons,
        }
    replaced = (
        session.query(SubstanceClassificationRow)
        .filter(
            SubstanceClassificationRow.substance_id == substance.id,
            SubstanceClassificationRow.source == SOURCE,
            SubstanceClassificationRow.locked_at.is_(None),
        )
        .delete(synchronize_session=False)
    )
    for item in result.classifications:
        session.add(SubstanceClassificationRow(
            id=str(uuid.uuid4()),
            substance_id=substance.id,
            hazard_class=item.hazard_class,
            category=item.category,
            h_code=item.h_code,
            qualifier=item.qualifier,
            source=SOURCE,
            source_ref=result.source_ref,
            confidence=CONFIDENCE,
            release_id=None,
        ))
    session.flush()
    return {
        "substance_id": substance.id,
        "status": "enriched",
        "written": len(result.classifications),
        "replaced": replaced,
        "h_codes": result.h_codes,
        "p_codes": result.p_codes,
        "signal": result.signal,
        "unmapped": result.unmapped,
        "review_reasons": result.review_reasons,
        "cid": result.cid,
    }


def enrich_substance(session, substance_id: str, *, client: httpx.Client | None = None) -> dict:
    """Fetch PubChem GHS for one substance and apply it. Flushes; caller commits."""
    substance = (
        session.query(SubstanceRow).filter(SubstanceRow.id == substance_id).first()
        if substance_id
        else None
    )
    if substance is None:
        raise PubChemNotFound(f"substance {substance_id} not found")
    query = substance.cas_no or substance.name_en or substance.name_zh
    if not query:
        raise PubChemNotFound(f"substance {substance_id} has no CAS or name to query")
    result = fetch_pubchem_ghs(query, client=client)
    outcome = apply_pubchem_enrichment(session, substance, result)
    outcome["query"] = query
    return outcome


def enrich_substance_by_id(substance_id: str, *, client: httpx.Client | None = None) -> dict:
    """Celery-task entry point: own session, fetch, apply, commit."""
    from ....db.database import default_session_factory

    with default_session_factory()() as session:
        outcome = enrich_substance(session, substance_id, client=client)
        session.commit()
        return outcome
