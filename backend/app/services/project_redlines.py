"""Project red-line (compliance constraint) enforcement, shared by save / recommend / substitution / release gate.

补齐计划 Phase 1D (1-11~1-14): the project's compliance constraints live in one
place — ``ProjectRow.payload.workspace.spec_contract`` (the SpecContract the
SpecStudioPage edits: ``red_lines``, ``voc_limit_g_l``, ``forbidden_pictograms``,
``forbidden_signal_word``). Previously only the frontend checked them, and the
release gate checked only substance ``red_lines`` — the GHS fields were dropped
whenever a spec was copied (frontend ProductDetailPage ``copy_spec`` and
``winning.CARRIED_SPEC_KEYS``), and the server never re-checked on save.

Severity model (one shared definition, every consumer honours it):
- ``block`` — exact / deterministic violation: substance over its cap, forbidden
  pictogram, forbidden signal word, RoHS hit, VOC over the limit when the VOC
  value is authoritative (a supplied ``voc_gpl``).
- ``warn`` — VOC over the limit on an *estimate* only (see :func:`estimate_voc_g_l`).
  Consumers decide: save blocks on ``block`` and surfaces ``warn``; the gate
  fails on any hit (red lines are never waivable); recommend filters on any hit;
  substitution demotes on any hit.

VOC estimate (documented approximation, never presented as measured):
``VOC (g/L) = density (g/L) x sum(weight fraction of VOC contributors)`` where
VOC contributors are ingredients with role solvent / co-solvent / coalescent,
excluding water. Density defaults to 1000 g/L (water-like); a caller-supplied
``density_g_cm3`` (e.g. from ``form.predicted``) overrides it. Because the
density is a guess, estimate-based VOC hits are ``warn``, not ``block``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# Roles that count as VOC contributors (GB 30981: volatile organic compounds).
# Water is never a VOC contributor.
VOC_ROLES = {"solvent", "co-solvent", "cosolvent", "coalescent", "成膜助剂"}
WATER_NAMES = {"water", "水", "去离子水", "deionized water", "di water", "纯水", "蒸馏水", "distilled water"}
DEFAULT_DENSITY_G_L = 1000.0


@dataclass
class GhsConstraints:
    """A project's compliance constraints, normalised from a spec_contract."""

    substances: list[dict[str, Any]] = field(default_factory=list)
    forbidden_pictograms: list[str] = field(default_factory=list)
    forbidden_signal_word: str | None = None
    voc_limit_g_l: float | None = None
    source: str = "none"  # "spec_contract" | "requirement" | "spec_snapshot" | "none"

    def __bool__(self) -> bool:
        return bool(self.substances or self.forbidden_pictograms or self.forbidden_signal_word
                    or self.voc_limit_g_l is not None)


@dataclass
class RedlineHit:
    code: str  # "substance" | "pictogram" | "signal_word" | "voc" | "rohs"
    message: str
    severity: str = "block"  # "block" | "warn"
    voc_estimated: bool = False


def constraints_from_spec_contract(spec: dict[str, Any] | None, *, source: str = "spec_contract") -> GhsConstraints:
    """Parse a spec_contract / spec_snapshot dict (flat SpecContract shape)."""
    spec = spec or {}
    voc = spec.get("voc_limit_g_l")
    return GhsConstraints(
        substances=list(spec.get("red_lines") or []),
        forbidden_pictograms=list(spec.get("forbidden_pictograms") or []),
        forbidden_signal_word=spec.get("forbidden_signal_word"),
        voc_limit_g_l=float(voc) if voc is not None else None,
        source=source,
    )


def load_project_constraints(session: Session, project_id: str | None) -> GhsConstraints:
    """Load a project's GHS constraints from its spec_contract (fail-open).

    VOC falls back to the legacy ``Requirement.voc_limit_gpl`` when the spec
    contract sets no ``voc_limit_g_l``. A missing / unreadable project yields
    empty constraints (no enforcement), never an exception — callers must not
    fail a save / recommend on a missing project record.
    """
    if not project_id:
        return GhsConstraints()
    try:
        from ..db.models import ProjectRow

        row = session.get(ProjectRow, project_id)
        if row is None:
            return GhsConstraints()
        # P0-3: ProjectRow.payload IS the flat workspace dict (project_store
        # writes workspace.model_dump()); it was never nested under a
        # "workspace" key, so the nested read always returned {} and no
        # spec-contract constraint ever applied.
        ws = row.payload or {}
        out = constraints_from_spec_contract(ws.get("spec_contract") or {}, source="spec_contract")
        if out.voc_limit_g_l is None:
            req = ws.get("requirement") or {}
            legacy = req.get("voc_limit_gpl")
            if legacy is not None:
                out.voc_limit_g_l = float(legacy)
                out.source = "requirement"
        return out
    except Exception:
        return GhsConstraints()


def estimate_voc_g_l(ingredients: list[dict[str, Any]], *, density_g_cm3: float | None = None) -> tuple[float, list[str], float]:
    """Estimate VOC (g/L) from ingredient roles. Returns (voc, contributors, density_g_l).

    Contributors are solvent-role ingredients excluding water. ``density_g_cm3``
    overrides the 1000 g/L default when the caller knows the product density.
    """
    density = float(density_g_cm3) * 1000.0 if density_g_cm3 else DEFAULT_DENSITY_G_L
    contributors: list[str] = []
    voc_frac = 0.0
    for ing in ingredients or []:
        role = str(ing.get("role") or "").strip().lower()
        name = str(ing.get("name") or "").strip()
        # Accept both Formulation-ingredient shape (weight_pct) and GHS shape (pct).
        pct = ing.get("weight_pct")
        if pct is None:
            pct = ing.get("pct")
        if role in VOC_ROLES and name.lower() not in WATER_NAMES and role != "water":
            voc_frac += float(pct or 0) / 100.0
            contributors.append(name)
    return round(density * voc_frac, 2), contributors, density


def check_redlines(
    items: list[dict[str, Any]],
    constraints: GhsConstraints,
    *,
    classification: dict[str, Any] | None = None,
    voc_gpl: float | None = None,
    voc_items: list[dict[str, Any]] | None = None,
    include_rohs: bool = True,
) -> list[RedlineHit]:
    """Check a composition against project constraints (pure: no DB, no classification).

    ``items`` are GHS-shape dicts (``name`` / ``pct`` / ``cas``) and may also carry
    ``role`` / ``weight_pct`` (Formulation-ingredient shape). ``classification``
    is a ``classify()`` result dict (``pictograms`` / ``signal_word``); when it is
    None, pictogram / signal-word checks are skipped (caller reports that).
    ``voc_gpl`` is an authoritative VOC value; without it the VOC check uses the
    role-based estimate over ``voc_items`` (defaults to ``items``) and any hit is
    ``warn``.
    """
    hits: list[RedlineHit] = []
    if not constraints:
        return hits
    by_cas: dict[str, float] = {}
    for it in items or []:
        cas = it.get("cas")
        if cas:
            by_cas[cas] = by_cas.get(cas, 0.0) + float(it.get("pct") or 0)
    for rl in constraints.substances:
        # Phase 1D (1-16): a red line may reference a compliance-list entry
        # ({"list_id", "list_entry_id"}) instead of a single CAS. A list entry
        # is a CLASS: sum the composition over all its CAS numbers.
        if rl.get("list_id") or rl.get("list_entry_id"):
            from .compliance_lists import class_total_for_redline, unresolved_redline_reason

            resolved = class_total_for_redline(by_cas, rl)
            if resolved is not None:
                found, label = resolved
                limit = float(rl.get("max_pct") or 0)
                if found > limit:
                    hits.append(RedlineHit(
                        code="substance",
                        message=f"{label}：合计 {found:g}% 超过红线 {limit:g}%"
                                + (f"（{rl['reason']}）" if rl.get("reason") else ""),
                    ))
            elif (reason := unresolved_redline_reason(rl)) is not None:
                # P1-24 fail-closed: an unresolvable reference must block,
                # never silently waive the red line (e.g. a typo'd entry id).
                hits.append(RedlineHit(code="substance", message=reason))
            continue
        cas = rl.get("cas")
        limit = float(rl.get("max_pct") or 0)
        found = by_cas.get(cas or "", 0.0)
        if cas and found > limit:
            hits.append(RedlineHit(
                code="substance",
                message=f"{rl.get('name') or cas}：{found:g}% 超过红线 {limit:g}%"
                        + (f"（{rl['reason']}）" if rl.get("reason") else ""),
            ))
    if include_rohs:
        from .compliance_rules import rohs_restricted

        seen: set[str] = set()
        for it in items or []:
            hit = rohs_restricted(it.get("name") or "", it.get("cas"))
            if hit and hit not in seen:
                seen.add(hit)
                hits.append(RedlineHit(code="rohs", message=f"{it.get('name')}：{hit}"))
    if classification is not None:
        pictos = [p.get("code") for p in classification.get("pictograms", []) if isinstance(p, dict)]
        bad = sorted(set(pictos) & set(constraints.forbidden_pictograms))
        if bad:
            hits.append(RedlineHit(code="pictogram", message=f"出现禁止的象形图 {', '.join(bad)}"))
        sw = classification.get("signal_word")
        fsw = constraints.forbidden_signal_word
        if fsw and sw and (sw == fsw or fsw == "警告"):
            hits.append(RedlineHit(code="signal_word", message=f"信号词为“{sw}”，违反规格契约"))
    if constraints.voc_limit_g_l is not None:
        if voc_gpl is not None:
            voc, estimated = float(voc_gpl), False
        else:
            voc, _contrib, _density = estimate_voc_g_l(voc_items if voc_items is not None else items)
            estimated = True
        if voc > constraints.voc_limit_g_l:
            basis = "估算值" if estimated else "测定/预测值"
            hits.append(RedlineHit(
                code="voc",
                message=f"VOC {voc:g} g/L 超过上限 {constraints.voc_limit_g_l:g} g/L（{basis}，基于申报溶剂角色）",
                severity="warn" if estimated else "block",
                voc_estimated=estimated,
            ))
    return hits


def formulation_to_items(form: Any) -> list[dict[str, Any]]:
    """Convert a Formulation (Pydantic) to check items (GHS shape + role/weight_pct)."""
    items: list[dict[str, Any]] = []
    for ing in getattr(form, "ingredients", []) or []:
        if isinstance(ing, dict):
            name = ing.get("name") or ""
            pct = float(ing.get("weight_pct") or 0)
            cas = ing.get("cas_no")
            role = ing.get("role") or ""
            material_id = ing.get("material_id")
            substance_id = ing.get("substance_id")
        else:
            name = getattr(ing, "name", "") or ""
            pct = float(getattr(ing, "weight_pct", 0) or 0)
            cas = getattr(ing, "cas_no", None)
            role = getattr(ing, "role", "") or ""
            material_id = getattr(ing, "material_id", None)
            substance_id = getattr(ing, "substance_id", None)
        items.append({
            "name": name, "pct": pct, "weight_pct": pct, "cas": cas, "role": role,
            "material_id": material_id, "substance_id": substance_id,
        })
    return items


def check_formulation_redlines(
    session: Session,
    items: list[dict[str, Any]],
    constraints: GhsConstraints,
    *,
    voc_gpl: float | None = None,
    voc_items: list[dict[str, Any]] | None = None,
    classify: bool = True,
) -> tuple[list[RedlineHit], bool]:
    """Full red-line check with GHS classification. Returns (hits, classification_available).

    Classification failure is fail-open: pictogram / signal-word checks are
    skipped and the caller is told via ``classification_available=False`` (save
    surfaces a warning; the gate fails closed on it separately).
    """
    if not constraints:
        return [], True
    needs_classification = bool(constraints.forbidden_pictograms or constraints.forbidden_signal_word)
    classification: dict[str, Any] | None = None
    available = True
    if classify and needs_classification:
        try:
            from .plm.ghs import service as ghs_service

            result, _built = ghs_service.build_and_classify(session, items)
            classification = result.to_dict()
        except Exception:
            available = False
    hits = check_redlines(items, constraints, classification=classification, voc_gpl=voc_gpl,
                          voc_items=voc_items)
    return hits, available
