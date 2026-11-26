"""Material substitution: what could replace this, and what would it cost me?

Two mechanisms existed before this and neither is usable as an answer. The
knowledge graph can surface a ``substitutes`` edge, but only if the semantic
layer is switched on and a regex matched the sentence — and it is
property-blind, so it will offer a replacement that misses your targets
entirely. The chemist agent has a four-entry hardcoded table covering exactly
one scenario (solvent-borne components in a waterborne system).

The differentiator here is the second signal: rather than reporting that two
molecules look alike, rebuild the formulation with the candidate swapped in
and report **what changes on every metric**. "Salt spray −8%, cost −22%,
VOC +5%" is actionable; "Tanimoto 0.81" is not.

Three signals are fused:

1. structural — substitute_group, functional_class, Hansen distance, and
   Tanimoto when RDKit is present
2. predicted property delta — the swap, re-simulated
3. literature evidence — knowledge-graph substitution edges with citations

Signal 2 carries a ``delta_confidence`` because its resolution depends on what
is installed; see ``_delta_confidence``.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from loguru import logger

from ..domain.genome import FormulationGenome
from ..domain.schemas import Requirement
from .plm.ghs.label_elements import severity as _category_severity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Structural score weights. substitute_group is a curated judgement that these
# materials are interchangeable, so it outranks every inferred signal.
_W_GROUP = 0.5
_W_CLASS = 0.25
_W_HANSEN = 0.15
_W_TANIMOTO = 0.10

# v16 P3-13: 按 domain 动态加权 —— 溶解性主导的场景（如涂料溶剂替换）
# 提高 Hansen 权重；官能团主导的场景提高 class 权重。
_DOMAIN_WEIGHT_OVERRIDES = {
    # 溶剂替换：溶解性是第一性原理
    "solvent": {"hansen": 0.35, "group": 0.35, "class": 0.20, "tanimoto": 0.10},
    # 涂料成膜：官能团兼容性优先
    "anticorrosion_coating": {"hansen": 0.20, "group": 0.45, "class": 0.25, "tanimoto": 0.10},
}


def _weights_for(domain: str | None) -> dict[str, float]:
    """v16 P3-13: domain 相关的权重；未知 domain 用默认权重。"""
    if domain:
        override = _DOMAIN_WEIGHT_OVERRIDES.get(str(domain).lower())
        if override:
            return {
                "group": override["group"],
                "class": override["class"],
                "hansen": override["hansen"],
                "tanimoto": override["tanimoto"],
            }
    return {
        "group": _W_GROUP,
        "class": _W_CLASS,
        "hansen": _W_HANSEN,
        "tanimoto": _W_TANIMOTO,
    }

# Hansen distance (MPa^0.5) beyond which two materials are treated as
# unrelated. ~8 is the usual "outside the solubility sphere" radius.
_HANSEN_LIMIT = 8.0


def hansen_distance(a: dict, b: dict) -> float | None:
    """Hansen solubility distance Ra = sqrt(4Δd² + Δp² + Δh²).

    The dispersion term is weighted ×4 by convention (Hansen's own fit). None
    when either material lacks parameters — most of the catalog does, so
    callers must treat this as an optional signal rather than a default.
    """
    try:
        dd = float(a["hansen_d"]) - float(b["hansen_d"])
        dp = float(a["hansen_p"]) - float(b["hansen_p"])
        dh = float(a["hansen_h"]) - float(b["hansen_h"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(math.sqrt(4.0 * dd * dd + dp * dp + dh * dh), 4)


def _tanimoto(a: dict, b: dict) -> float | None:
    """Morgan/Tanimoto similarity, when the chemistry gateway can provide it."""
    from . import chemtools

    try:
        capable = chemtools.availability()["capabilities"]["mol_similarity"]["available"]
    except Exception:
        return None
    if not capable:
        return None
    smiles_a, smiles_b = a.get("smiles"), b.get("smiles")
    if not smiles_a or not smiles_b:
        return None
    return chemtools.mol_similarity(smiles_a, smiles_b)


def structural_score(
    a: dict, b: dict, *, domain: str | None = None
) -> tuple[float, dict]:
    """Similarity in [0, 1] plus the per-signal breakdown that produced it.

    Renormalised over the signals actually available, so a material with no
    Hansen data is not penalised relative to one that has it.

    v16 P3-13: domain 动态加权（None = 默认权重，向后兼容）。
    """
    _w = _weights_for(domain)
    parts: dict[str, float] = {}
    total = 0.0
    weight = 0.0

    if a.get("substitute_group") and b.get("substitute_group"):
        hit = 1.0 if a["substitute_group"] == b["substitute_group"] else 0.0
        parts["substitute_group"] = hit
        total += _w['group'] * hit
        weight += _w['group']

    if a.get("functional_class") and b.get("functional_class"):
        hit = 1.0 if a["functional_class"] == b["functional_class"] else 0.0
        parts["functional_class"] = hit
        total += _w['class'] * hit
        weight += _w['class']

    distance = hansen_distance(a, b)
    if distance is not None:
        closeness = max(0.0, 1.0 - distance / _HANSEN_LIMIT)
        parts["hansen"] = round(closeness, 4)
        parts["hansen_distance"] = distance
        total += _w['hansen'] * closeness
        weight += _w['hansen']

    tanimoto = _tanimoto(a, b)
    if tanimoto is not None:
        parts["tanimoto"] = tanimoto
        total += _w['tanimoto'] * tanimoto
        weight += _w['tanimoto']

    return (round(total / weight, 4) if weight else 0.0), parts


def _delta_confidence(a: dict, b: dict) -> str:
    """How much resolution the predicted delta actually has.

    The mechanistic predictor sees material identity through role sums, the
    amine/epoxy ratio, and the price/VOC lookup. Molecular descriptors would
    add real chemical resolution, but they need RDKit and the v2 feature block.
    Without those, swapping two same-role materials moves cost and VOC and
    leaves the performance metrics untouched — so the report must not present
    an unchanged salt-spray figure as evidence the swap is performance-neutral.
    """
    from . import chemtools

    try:
        rdkit_ok = bool(chemtools.availability().get("rdkit_installed"))
    except Exception:
        rdkit_ok = False
    if rdkit_ok:
        return "high"
    if a.get("role") != b.get("role"):
        return "low"
    return "cost_only"


def _metric_deltas(before: dict, after: dict) -> dict[str, dict]:
    """Per-metric change. Metrics present on only one side are reported as
    appearing/disappearing rather than as a delta from zero — the predictor's
    key set is domain-dependent and conditionally sparse. Non-numeric values
    (string metadata such as ``delta_e_method``) carry no delta."""
    out: dict[str, dict] = {}
    for metric in sorted(set(before) | set(after)):
        old, new = before.get(metric), after.get(metric)
        if old is None or new is None:
            out[metric] = {"before": old, "after": new, "delta": None, "pct": None}
            continue
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) \
                or isinstance(old, bool) or isinstance(new, bool):
            out[metric] = {"before": old, "after": new, "delta": None, "pct": None}
            continue
        delta = new - old
        pct = (delta / old * 100.0) if old else None
        out[metric] = {
            "before": round(old, 4),
            "after": round(new, 4),
            "delta": round(delta, 4),
            "pct": round(pct, 2) if pct is not None else None,
        }
    return out


def _requirement_fit(candidate: dict, req: Requirement | None) -> float:
    """Soft score: how well candidate deltas move toward requirement objectives.

    Higher is better. Missing objectives / deltas → 0 (no penalty).
    For ranking only; the value has no absolute meaning and is not
    comparable across different numbers of objectives.
    """
    if req is None:
        return 0.0
    objectives = list(getattr(req, "objectives", None) or [])
    if not objectives:
        # Domain heuristics when the caller did not declare objectives.
        domain = getattr(getattr(req, "domain", None), "value", None) or str(
            getattr(req, "domain", "") or ""
        )
        if domain == "degreaser":
            objectives = [
                type("O", (), {"metric": "cleaning_efficiency", "direction": "maximize"})(),
                type("O", (), {"metric": "cost_cny_per_kg", "direction": "minimize"})(),
            ]
        else:
            objectives = [
                type("O", (), {"metric": "salt_spray_hours", "direction": "maximize"})(),
                type("O", (), {"metric": "voc_gpl", "direction": "minimize"})(),
                type("O", (), {"metric": "cost_cny_per_kg", "direction": "minimize"})(),
            ]
        voc_limit = getattr(req, "voc_limit_gpl", None)
        if voc_limit is not None:
            # Prefer stronger VOC reduction when a limit is set.
            objectives = [
                type("O", (), {"metric": "voc_gpl", "direction": "minimize"})(),
                *objectives,
            ]

    deltas = candidate.get("deltas") or {}
    score = 0.0
    for obj in objectives:
        metric = getattr(obj, "metric", None) or ""
        direction = (getattr(obj, "direction", None) or "maximize").lower()
        pct = (deltas.get(metric) or {}).get("pct")
        if pct is None:
            continue
        try:
            pct_f = float(pct)
        except (TypeError, ValueError):
            continue
        if direction == "minimize":
            score += -pct_f
        else:
            score += pct_f
    return round(score, 4)


def _kg_evidence(original: str, candidate: str) -> list[dict]:
    """Literature-backed substitution edges, when the graph is enabled."""
    from .kg.retrieval import kg_enabled

    if not kg_enabled():
        return []
    try:
        import re

        from ..db.entity_store import get_entity_store
        from .kg.graph_query import discover_substitutes

        def _cid(name: str) -> str:
            return f"chem:catalog:{re.sub(r'[^a-zA-Z0-9]+', '_', name.lower())[:80]}"

        store = get_entity_store()
        entity_id = _cid(original)
        if store.get_entity(entity_id) is None:
            return []
        target = _cid(candidate)
        out: list[dict] = []
        for cand in discover_substitutes(entity_id, limit=25).substitutes:
            if cand.entity_id != target:
                continue
            for step in cand.path:
                for ev in step.relation.evidence or []:
                    out.append(
                        {
                            "source_id": ev.source_id,
                            "chunk_id": ev.chunk_id,
                            "sentence": ev.sentence,
                            "confidence": ev.confidence,
                        }
                    )
        return out[:5]
    except Exception as exc:
        logger.debug("substitution: KG evidence unavailable ({})", exc)
        return []


def _mark_advisory(rows: list[dict] | None) -> list[dict]:
    """P2-2 (review 2026-11-26): stamp every non-catalog row as unscreened.

    Non-catalog layers (external / literature / SureChEMBL / LLM) carry
    similarity or textual evidence only — no feasibility verdict, no
    red-line check, no GHS preview. ``screened: false`` /
    ``advisory: true`` makes that explicit in the response so no consumer
    can mistake these rows for screened catalog candidates. Any
    ``feasible`` / ``blocking_reasons`` key a fetcher may have attached
    is dropped: only the L1 catalog path may assert feasibility.
    """
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row["screened"] = False
        row["advisory"] = True
        row.pop("feasible", None)
        row.pop("blocking_reasons", None)
    return rows or []


def _light_screen_rows(rows: list[dict] | None) -> None:
    """P2-2 optional light screen: CAS-only RoHS/SVHC presence check.

    For rows carrying a CAS, reuse the deterministic
    :func:`compliance_rules.check_compliance` on a synthetic
    single-ingredient formulation at 100 wt% (pure-substance basis): a
    hit means the candidate material itself is a listed restricted
    substance, so it cannot be used above the limit. The result lands in
    ``light_screen`` (``checked`` / ``status`` / ``rohs_hits`` /
    ``svhc_hits`` / ``reasons``); rows without a CAS get
    ``checked: false`` with reason ``no_cas``. This is *not* full
    screening — there is no swapped-formulation GHS preview for
    non-catalog materials (no substance record to classify) — so rows
    keep ``screened: false`` / ``advisory: true`` regardless of the
    light-screen outcome. Fail-open: any error leaves a
    ``checked: false`` record and never blocks the report.
    """
    if not rows:
        return
    try:
        from types import SimpleNamespace

        from .compliance_rules import check_compliance
    except Exception as exc:
        logger.debug("substitution light screen unavailable ({})", exc)
        for row in rows:
            if isinstance(row, dict):
                row["light_screen"] = {"checked": False, "reason": f"screen_unavailable:{exc}"}
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        cas = str(row.get("cas_no") or "").strip()
        if not cas:
            row["light_screen"] = {"checked": False, "reason": "no_cas"}
            continue
        try:
            form = SimpleNamespace(
                ingredients=[
                    SimpleNamespace(
                        name=str(row.get("name") or ""),
                        cas_no=cas,
                        weight_pct=100.0,
                    )
                ]
            )
            result = check_compliance(form)
            row["light_screen"] = {
                "checked": True,
                "basis": "pure_substance_100pct",
                "status": result.status,
                "rohs_hits": list(result.rohs_hits),
                "svhc_hits": list(result.svhc_hits),
                "reasons": list(result.reasons),
            }
        except Exception as exc:
            logger.debug("substitution light screen failed for {} ({})", row.get("name"), exc)
            row["light_screen"] = {"checked": False, "reason": f"screen_error:{exc}"}


def _catalog_layer(
    genome: FormulationGenome,
    slot_index: int,
    req: Requirement | None,
    *,
    session: Session | None,
    include_unavailable: bool,
) -> tuple[dict, list[dict], list[tuple[dict, object]], object, dict]:
    """P3-2: L1 库内层 —— 召回同 substitute_group / 同 role 的库内材料，逐个换料重算
    （结构分、指标 delta、可行性、供应标注、需求拟合），共享尺度重打分，项目红线标注，
    按 feasible / requirement_fit / 红线 / stale / 结构分 / score_after 排序。

    返回 ``(context, candidates, scored_forms, base_form, base_metrics)``；context
    携带 orchestrator 与后续层共用的槽位信息（original / original_spec /
    original_in_catalog / group / role / pool）。纯搬移，不改行为。
    """
    from ..domain import knowledge
    from ..pipeline import reconstruct
    from ..pipeline.workflow import _score_and_validate, process_for
    from .feasibility import check_formulation

    if not 0 <= slot_index < len(genome.slots):
        raise IndexError(f"slot index {slot_index} out of range")

    slot = genome.slots[slot_index]
    original = slot.material
    original_spec = dict(knowledge.RAW_MATERIALS.get(original) or {})
    original_in_catalog = bool(original_spec) or original in knowledge.RAW_MATERIALS
    target = req if req is not None else genome.domain
    process = process_for(req) if req is not None else {}

    base_form = _score_and_validate(
        reconstruct.formulation_from_genome(target, genome, strict=False),
        process,
        req,
        enrich_network=False,
    )
    base_metrics = dict(base_form.predicted)

    # Recall: same substitute_group, else same role. Group membership is the
    # curated signal, so it takes precedence over the coarser role match.
    # Uncatalogued originals (LLM recommend) have empty group → role pool.
    group = original_spec.get("substitute_group")
    role = original_spec.get("role") or slot.role or "additive"
    pool: list[str] = []
    for name, spec in knowledge.RAW_MATERIALS.items():
        if name == original:
            continue
        if not include_unavailable and spec.get("availability") == "discontinued":
            continue
        if group and spec.get("substitute_group") == group:
            pool.append(name)
        elif not group and spec.get("role") == role:
            pool.append(name)

    candidates: list[dict] = []
    scored_forms: list[tuple[dict, object]] = []  # (candidate row, its scored Formulation)
    # v18-11: 提取 domain 传给 structural_score，否则 _DOMAIN_WEIGHT_OVERRIDES 永不生效。
    _domain = None
    if req is not None:
        _domain = getattr(req.domain, "value", None) or str(req.domain)
    for name in pool:
        spec = dict(knowledge.RAW_MATERIALS.get(name) or {})
        score, breakdown = structural_score(original_spec, spec, domain=_domain)
        swapped = genome.with_material(slot_index, name)
        try:
            form = reconstruct.formulation_from_genome(target, swapped, strict=False)
        except Exception:
            continue
        try:
            verdict = check_formulation(form, req)
            form = _score_and_validate(form, process, req, enrich_network=False)
        except Exception as exc:
            logger.debug("substitution candidate {} skipped ({})", name, exc)
            continue
        cand = {
            "material": name,
            "zh_name": spec.get("zh_name"),
            "role": spec.get("role"),
            "functional_class": spec.get("functional_class"),
            "substitute_group": spec.get("substitute_group"),
            "availability": spec.get("availability", "in_stock"),
            "supplier": spec.get("supplier"),
            "structural_score": score,
            "structural_breakdown": breakdown,
            "deltas": _metric_deltas(base_metrics, form.predicted),
            "delta_confidence": _delta_confidence(original_spec, spec),
            "feasible": verdict.feasible,
            "blocking_reasons": [] if verdict.feasible else verdict.reasons,
            "evidence": _kg_evidence(original, name),
            "score_after": form.score,
            "source": "catalog",
            # P2-2: catalog candidates passed feasibility (+ red-line /
            # GHS preview when a session is supplied) — screened, not advisory.
            "screened": True,
            "advisory": False,
        }
        # Post-A′ #1: annotate stale/missing price; light demotion only.
        try:
            from .supply_flags import STALE_SCORE_PENALTY, supply_annotation_for_material

            ann = supply_annotation_for_material(name)
            cand["supply_badges"] = list(ann.get("badges") or [])
            cand["stale_price"] = bool(ann.get("stale_price"))
            cand["missing_price"] = bool(ann.get("missing_price"))
            cand["long_lead_time"] = bool(ann.get("long_lead_time"))
            if ann.get("stale_price") or ann.get("missing_price"):
                cand["structural_score"] = round(
                    max(0.0, float(cand["structural_score"]) - STALE_SCORE_PENALTY), 4
                )
        except Exception:
            cand["supply_badges"] = []
            cand["stale_price"] = False
            cand["missing_price"] = False
            cand["long_lead_time"] = False
        cand["requirement_fit"] = _requirement_fit(cand, req)
        candidates.append(cand)
        scored_forms.append((cand, form))

    # _score_and_validate normalises each metric without a user-given range against *that
    # candidate's own* predicted value, so a weak and a strong swap both score 0.5 and
    # ``score_after`` could not tell them apart. Put every swap — and the recipe they replace —
    # on one shared ruler. (A no-op for a single maximize objective, whose score is the raw value.)
    if scored_forms:
        from ..domain.project_spec import normalize_requirement
        from .recommend_pipeline import rescore_with_shared_bounds

        objectives = normalize_requirement(req).objectives if req is not None else None
        rescore_with_shared_bounds([base_form, *(f for _c, f in scored_forms)], objectives, process)
        for cand, form in scored_forms:
            cand["score_after"] = form.score

    # Phase 1D (1-11): project red-line annotation + demotion. Each swapped
    # formulation is checked against the owning project's constraints; a hit
    # annotates the candidate (``redline_hits``) and demotes it in the sort.
    # Substitution is advisory, so violating candidates are shown, not dropped.
    # Fail-open: without a session (or on check failure) no annotation is added.
    if session is not None and req is not None and getattr(req, "project_id", None):
        try:
            from .project_redlines import (
                check_formulation_redlines,
                formulation_to_items,
                load_project_constraints,
            )

            _constraints = load_project_constraints(session, req.project_id)
            if _constraints:
                for cand, form in scored_forms:
                    _hits, _ = check_formulation_redlines(
                        session, formulation_to_items(form), _constraints,
                        voc_gpl=(getattr(form, "predicted", None) or {}).get("voc_gpl"))
                    cand["redline_hits"] = [{"code": h.code, "message": h.message} for h in _hits]
                    cand["redline_violated"] = bool(_hits)
        except Exception as exc:
            logger.warning("substitution red-line check failed (fail-open): {}", exc)
    for cand, _form in scored_forms:
        cand.setdefault("redline_hits", [])
        cand.setdefault("redline_violated", False)

    # Feasible first; then requirement-fit; then prefer non-stale; then structural / score.
    candidates.sort(
        key=lambda c: (
            not c["feasible"],
            -float(c.get("requirement_fit") or 0.0),
            bool(c.get("redline_violated")),
            bool(c.get("stale_price")),
            bool(c.get("missing_price")),
            -c["structural_score"],
            -(c["score_after"] or 0.0),
            c["material"],
        )
    )

    return (
        {
            "original": original,
            "original_spec": original_spec,
            "original_in_catalog": original_in_catalog,
            "group": group,
            "role": role,
            "pool": pool,
        },
        candidates,
        scored_forms,
        base_form,
        base_metrics,
    )


def _classification_preview(
    session: Session | None,
    candidates: list[dict],
    scored_forms: list[tuple[dict, object]],
    base_form: object,
    classification_preview_limit: int,
) -> None:
    """P3-2: 分类预览层 —— 对排序后前 N 个库内候选，用替换后配方重算 GHS 分类并与
    替换前对比（``classification_before`` / ``classification_after`` /
    ``classification_changed`` / ``classification_upgraded``）。仅 session 提供时做；
    分类失败 fail-open（该候选无预览键）。纯搬移，不改行为。
    """
    # 补齐计划 2-7: 替换后分类预览 —— 对排序后前 N 个库内候选，用替换后配方重算
    # GHS 分类并与替换前对比。仅 session 提供时做（build_and_classify 需要 session）；
    # 分类失败 fail-open（该候选无预览键）。外部/文献候选不在库内配方中，不做预览。
    if session is not None and classification_preview_limit and classification_preview_limit > 0:
        try:
            from .plm.ghs.service import build_and_classify
            from .project_redlines import formulation_to_items

            form_by_cand = {id(c): f for c, f in scored_forms}
            base_result, _ = build_and_classify(session, formulation_to_items(base_form))
            base_summary = _classification_summary(base_result)
            for cand in candidates[:classification_preview_limit]:
                form = form_by_cand.get(id(cand))
                if form is None:
                    continue
                try:
                    after_result, _ = build_and_classify(session, formulation_to_items(form))
                    after_summary = _classification_summary(after_result)
                    cmp = compare_classifications(base_summary, after_summary)
                    cand["classification_before"] = base_summary
                    cand["classification_after"] = after_summary
                    cand["classification_changed"] = cmp["changed"]
                    cand["classification_upgraded"] = cmp["upgraded"]
                except Exception as exc:
                    logger.debug("substitution classification preview failed for {} ({})", cand.get("material"), exc)
        except Exception as exc:
            logger.debug("substitution classification preview skipped ({})", exc)


def _external_layer(
    original: str,
    original_spec: dict,
    role: str,
    *,
    include_external: bool,
    external_limit: int,
    similarity_threshold: int,
) -> tuple[dict, list[dict], dict]:
    """P3-2: L3 外部层 —— PubChem 结构相似物（``include_external``）。返回
    ``(identity, external, external_meta)``；``layers_used`` 由 orchestrator 记。
    纯搬移，不改行为。
    """
    identity: dict
    external: list[dict] = []
    external_meta: dict

    if include_external:
        from .external_alternatives import fetch_external_alternatives

        ext = fetch_external_alternatives(
            material=original,
            spec=original_spec,
            limit=external_limit,
            threshold=similarity_threshold,
            role_hint=str(role) if role else None,
        )
        identity = ext["identity"]
        external = list(ext.get("external") or [])
        external_meta = dict(ext.get("external_meta") or {})
    else:
        from .external_alternatives import resolve_slot_identity

        identity = resolve_slot_identity(original, original_spec, network=False)
        external_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_external=false",
            "provider": "pubchem_fastsimilarity_2d",
        }

    return identity, external, external_meta


def _literature_layer(
    original: str,
    original_spec: dict,
    role: str,
    identity: dict,
    *,
    include_literature: bool,
    literature_limit: int,
) -> tuple[list[dict], dict]:
    """P3-2: L2 文献层 —— 文献 / KG / kb_products 候选（``include_literature``）。
    失败降级为 queried=False 的 meta，不抛错。返回 ``(literature, literature_meta)``；
    ``layers_used`` 由 orchestrator 记。纯搬移，不改行为。
    """
    literature: list[dict] = []
    literature_meta: dict
    if include_literature:
        try:
            from .literature_alternatives import fetch_literature_alternatives

            lit = fetch_literature_alternatives(
                material=original,
                cas_no=str(identity.get("cas_no") or original_spec.get("cas_no") or ""),
                role_hint=str(role) if role else None,
                limit=literature_limit,
            )
            literature = list(lit.get("literature") or [])
            literature_meta = dict(lit.get("literature_meta") or {})
        except Exception as exc:
            logger.warning("literature substitutes degraded ({})", exc)
            literature = []
            literature_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"literature_error:{exc}",
                "providers": [],
            }
    else:
        literature_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_literature=false",
            "providers": [],
        }

    return literature, literature_meta


def _surechembl_layer(
    original: str,
    original_spec: dict,
    role: str,
    external: list[dict],
    identity: dict,
    *,
    include_surechembl: bool,
    surechembl_limit: int,
    similarity_threshold: int,
) -> tuple[list[dict], dict]:
    """P3-2: SureChEMBL 层 —— 结构相似物 + 专利文档（``include_surechembl``），并按
    SMILES / InChI key 与 PubChem 层轻去重。失败降级，不抛错。返回
    ``(surechembl_rows, surechembl_meta)``；``layers_used`` 由 orchestrator 记。
    纯搬移，不改行为。
    """
    surechembl_rows: list[dict] = []
    surechembl_meta: dict
    if include_surechembl:
        try:
            from .surechembl_alternatives import fetch_surechembl_alternatives

            smi = str(identity.get("smiles") or original_spec.get("smiles") or "") or None
            sch = fetch_surechembl_alternatives(
                material=original,
                smiles=smi,
                limit=surechembl_limit,
                threshold=similarity_threshold,
                role_hint=str(role) if role else None,
            )
            surechembl_rows = list(sch.get("surechembl") or [])
            surechembl_meta = dict(sch.get("surechembl_meta") or {})
            # Light dedup vs PubChem by SMILES / InChI key.
            ext_keys = {
                str(r.get("smiles") or "").strip()
                for r in external
                if r.get("smiles")
            } | {
                str(r.get("inchi_key") or "").strip()
                for r in external
                if r.get("inchi_key")
            }
            if ext_keys:
                surechembl_rows = [
                    r
                    for r in surechembl_rows
                    if (str(r.get("smiles") or "").strip() not in ext_keys)
                    and (str(r.get("inchi_key") or "").strip() not in ext_keys)
                ]
                surechembl_meta["count"] = len(surechembl_rows)
        except Exception as exc:
            logger.warning("surechembl substitutes degraded ({})", exc)
            surechembl_rows = []
            surechembl_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"surechembl_error:{exc}",
                "provider": "surechembl_api",
                "search_hash": None,
            }
    else:
        surechembl_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_surechembl=false",
            "provider": "surechembl_api",
            "search_hash": None,
        }

    return surechembl_rows, surechembl_meta


def _llm_layer(
    original: str,
    role: str,
    candidates: list[dict],
    literature: list[dict],
    external: list[dict],
    surechembl_rows: list[dict],
    *,
    include_llm: bool | None,
    llm_limit: int,
    limit: int,
) -> tuple[list[dict], dict]:
    """P3-2: L4 LLM 层 —— 库内命中 < 3 时自动跑，``include_llm`` 显式 true/false
    覆盖。已知名（库内前 limit + 各层行名）用于去重提示。失败降级，不抛错。
    返回 ``(llm_rows, llm_meta)``；``layers_used`` 由 orchestrator 记。纯搬移，不改行为。
    """
    # L4: auto when catalog hits are scarce; explicit true/false overrides.
    catalog_hits = len(candidates[:limit])
    if include_llm is True:
        run_llm, llm_mode = True, "forced"
    elif include_llm is False:
        run_llm, llm_mode = False, "off"
    else:
        run_llm, llm_mode = catalog_hits < 3, "auto"

    llm_rows: list[dict] = []
    llm_meta: dict
    if run_llm:
        try:
            from .llm_alternatives import fetch_llm_alternatives

            known = [c["material"] for c in candidates[:limit]]
            known.extend(r.get("name") or "" for r in literature)
            known.extend(r.get("name") or "" for r in external)
            known.extend(r.get("name") or "" for r in surechembl_rows)
            out = fetch_llm_alternatives(
                material=original,
                role_hint=str(role) if role else None,
                limit=llm_limit,
                known_names=known,
                allow_llm=True,
            )
            llm_rows = list(out.get("llm") or [])
            llm_meta = dict(out.get("llm_meta") or {})
            llm_meta["mode"] = llm_mode
        except Exception as exc:
            logger.warning("llm substitutes degraded ({})", exc)
            llm_rows = []
            llm_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"llm_error:{exc}",
                "mode": llm_mode,
                "providers": [],
            }
    else:
        reason = (
            "include_llm=false"
            if include_llm is False
            else f"auto_skipped:catalog_hits={catalog_hits}>=3"
        )
        llm_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": reason,
            "mode": llm_mode,
            "providers": [],
        }

    return llm_rows, llm_meta


def find_substitutes(
    genome: FormulationGenome,
    slot_index: int,
    req: Requirement | None = None,
    *,
    limit: int = 10,
    include_unavailable: bool = False,
    include_external: bool = True,
    external_limit: int = 8,
    similarity_threshold: int = 85,
    include_literature: bool = True,
    literature_limit: int = 8,
    include_surechembl: bool = True,
    surechembl_limit: int = 8,
    include_llm: bool | None = None,
    llm_limit: int = 5,
    session: Session | None = None,
    classification_preview_limit: int = 5,
) -> dict:
    """Rank replacements for one slot, each with its predicted property delta.

    Layers (four-layer funnel + SureChEMBL patents):
    - L1 catalog candidates with formula Δ (always)
    - L2 literature/KG/kb_products advisory list (``include_literature``)
    - L3 PubChem structure similars (``include_external``)
    - SureChEMBL structure similars + patent docs (``include_surechembl``)
    - L4 rules/LLM when L1 < 3 or ``include_llm`` forced (``include_llm``)

    补齐计划 2-7: ``classification_preview_limit`` > 0 且传入 session 时，对排序后
    前 N 个库内候选做替换后配方 GHS 重分类预览（``classification_before`` /
    ``classification_after`` / ``classification_changed`` / ``classification_upgraded``）。
    分类失败 fail-open（不附加预览键，候选照常返回）。

    P2-2 (review 2026-11-26): 筛查契约 —— 只有 ``candidates``（库内 L1）
    是已筛查候选（``screened: true``，带 ``feasible`` / ``blocking_reasons``，
    有 session 时另带红线与 GHS 重分类预览）；``external`` / ``literature`` /
    ``surechembl`` / ``llm`` 四层一律 ``screened: false`` / ``advisory: true``，
    不带 ``feasible``，仅对有 CAS 的行附加 ``light_screen``（纯物质口径的
    RoHS/SVHC 存在性检查，见 ``_light_screen_rows``），不作 GHS 预览。
    调用方不得把外部行与已筛查候选等同采纳。
    """
    if not 0 <= slot_index < len(genome.slots):
        raise IndexError(f"slot index {slot_index} out of range")

    ctx, candidates, scored_forms, base_form, base_metrics = _catalog_layer(
        genome, slot_index, req, session=session, include_unavailable=include_unavailable
    )
    original = ctx["original"]
    original_spec = ctx["original_spec"]
    original_in_catalog = ctx["original_in_catalog"]
    group = ctx["group"]
    role = ctx["role"]
    pool = ctx["pool"]

    _classification_preview(session, candidates, scored_forms, base_form, classification_preview_limit)

    layers_used: list[str] = ["catalog"]

    identity, external, external_meta = _external_layer(
        original, original_spec, role,
        include_external=include_external, external_limit=external_limit,
        similarity_threshold=similarity_threshold,
    )
    if external_meta.get("queried") and external:
        layers_used.append("external")

    literature, literature_meta = _literature_layer(
        original, original_spec, role, identity,
        include_literature=include_literature, literature_limit=literature_limit,
    )
    if literature:
        layers_used.append("literature")

    surechembl_rows, surechembl_meta = _surechembl_layer(
        original, original_spec, role, external, identity,
        include_surechembl=include_surechembl, surechembl_limit=surechembl_limit,
        similarity_threshold=similarity_threshold,
    )
    if surechembl_rows:
        layers_used.append("surechembl")

    llm_rows, llm_meta = _llm_layer(
        original, role, candidates, literature, external, surechembl_rows,
        include_llm=include_llm, llm_limit=llm_limit, limit=limit,
    )
    if llm_rows:
        layers_used.append("llm")

    # P2-2: every non-catalog layer is advisory-only — stamp the
    # screened/advisory markers and run the CAS light screen (RoHS/SVHC
    # presence) on each layer's rows. Markers are applied here, at the
    # funnel exit, so they hold even for rows produced by a fetcher that
    # does not stamp them itself (literature / SureChEMBL / LLM fetchers
    # are outside this change's file scope).
    for _rows in (external, literature, surechembl_rows, llm_rows):
        _mark_advisory(_rows)
        _light_screen_rows(_rows)

    return {
        "original": original,
        "original_in_catalog": original_in_catalog,
        "slot_index": slot_index,
        "role": role,
        "substitute_group": group,
        # predicted 含字符串/None 元数据指标（如 delta_e_method / 暂无值的色差），只对数值做 round。
        "base_metrics": {k: (round(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                         for k, v in base_metrics.items()},
        "candidates": candidates[:limit],
        "total_considered": len(pool),
        "identity": identity,
        "external": external,
        "external_meta": external_meta,
        "literature": literature,
        "literature_meta": literature_meta,
        "surechembl": surechembl_rows,
        "surechembl_meta": surechembl_meta,
        "llm": llm_rows,
        "llm_meta": llm_meta,
        "layers_used": layers_used,
    }


# 补齐计划 2-6/2-7: GHS 分类严重度比较（信号词 + 新增危害类别）。
# 信号词：危险 > 警告 > 无。新增 H 代码 = 新增危害 = 升级。
_SIGNAL_SEVERITY = {"危险": 2, "警告": 1, None: 0, "": 0}


def _classification_summary(result: object) -> dict:
    """Minimal classification fingerprint for before/after comparison.

    P3-1: ``categories``（H 代码 → 危害类别，如 ``{"H314": "1A"}``）只在结果携带
    hazard outcomes（``result.hazards``，见 ``plm.ghs.model.HazardOutcome``）时出现；
    同 H 代码的子类别变化靠它比较。无该数据的结果（旧基线、裸摘要）仍保持原有三键
    形态，比较退回信号词 + H 代码口径。同一 H 代码若分属多类，取最严类别。
    """
    summary = {
        "signal_word": getattr(result, "signal_word", None),
        "h_codes": sorted(getattr(result, "h_codes", None) or []),
        "pictograms": sorted(p.get("code") for p in (getattr(result, "pictograms", None) or []) if p.get("code")),
    }
    categories: dict[str, str] = {}
    for outcome in getattr(result, "hazards", None) or []:
        category = getattr(outcome, "category", None)
        if not category:
            continue
        for h in getattr(outcome, "h_codes", None) or []:
            prev = categories.get(h)
            if prev is None or _category_severity(str(category)) < _category_severity(str(prev)):
                categories[h] = str(category)
    if categories:
        summary["categories"] = categories
    return summary


def _category_changes(before: dict, after: dict) -> list[dict]:
    """P3-1: 同 H 代码的子类别变化（前后摘要都带 ``categories`` 时才可比）。

    任一侧缺失 ``categories`` 即返回 []（旧基线 fail-open，退回信号词 + H 代码口径）。
    每项 ``{"h_code", "category_before", "category_after"}``；方向（变严 / 变松）由
    调用方按 ``label_elements.severity`` 序判定（序值越小越严）。
    """
    cats_before = (before or {}).get("categories") or {}
    cats_after = (after or {}).get("categories") or {}
    out: list[dict] = []
    for h in sorted(set(cats_before) & set(cats_after)):
        cb, ca = str(cats_before[h]), str(cats_after[h])
        if cb != ca:
            out.append({"h_code": h, "category_before": cb, "category_after": ca})
    return out


def compare_classifications(before: dict, after: dict) -> dict:
    """Compare two classification summaries (2-6 classification_upgrade / 2-7 preview 共用).

    upgraded = 信号词升级，或出现新增危害类别（H 代码），或同 H 代码子类别变严
    （按 ``label_elements.severity`` 序，序值越小越严，如 1C→1A、2B→2A），或象形图
    新增。changed = 任何差异（含危害减少 / 信号词降级 / 子类别变松 —— 变松也是
    变化，但不是升级）。返回键保持 2-6 原状。
    """
    before = before or {}
    after = after or {}
    sig_before = _SIGNAL_SEVERITY.get(before.get("signal_word"), 0)
    sig_after = _SIGNAL_SEVERITY.get(after.get("signal_word"), 0)
    h_before = set(before.get("h_codes") or [])
    h_after = set(after.get("h_codes") or [])
    added = sorted(h_after - h_before)
    removed = sorted(h_before - h_after)
    cat_changes = _category_changes(before, after)
    cat_upgraded = any(
        _category_severity(c["category_after"]) < _category_severity(c["category_before"])
        for c in cat_changes
    )
    pic_before = set(before.get("pictograms") or [])
    pic_after = set(after.get("pictograms") or [])
    added_pictos = sorted(pic_after - pic_before)
    removed_pictos = sorted(pic_before - pic_after)
    upgraded = sig_after > sig_before or bool(added) or cat_upgraded or bool(added_pictos)
    changed = (
        upgraded or bool(removed) or sig_before != sig_after
        or bool(cat_changes) or bool(added_pictos) or bool(removed_pictos)
    )
    return {
        "changed": changed,
        "upgraded": upgraded,
        "added_h_codes": added,
        "removed_h_codes": removed,
        "signal_before": before.get("signal_word"),
        "signal_after": after.get("signal_word"),
    }


def _formulation_cost_per_kg(form: object, knowledge_mod: object) -> tuple[float, list[str]]:
    """配方成本估算（元/kg）= Σ weight_pct/100 × price_cny_per_kg。

    无价材料按 0 计并列入 unknown（成本为下限估算，调用方须在 detail 中声明）。
    """
    materials = getattr(knowledge_mod, "RAW_MATERIALS", {}) or {}
    total = 0.0
    unknown: list[str] = []
    for ing in getattr(form, "ingredients", None) or []:
        name = getattr(ing, "name", "") or ""
        pct = float(getattr(ing, "weight_pct", 0) or 0)
        price = (materials.get(name) or {}).get("price_cny_per_kg")
        if price is None:
            unknown.append(name)
            continue
        total += pct / 100.0 * float(price)
    return total, unknown


def scan_substitution_triggers(
    genomes: dict[str, FormulationGenome] | None = None,
    req: Requirement | None = None,
    *,
    session: Session | None = None,
    baseline_classifications: dict[str, dict] | None = None,
    cost_limit_per_kg: float | None = None,
    material_price_limits: dict[str, float] | None = None,
) -> dict:
    """补齐计划 2-6: 替换触发扫描 —— 断供之外再补超成本、命中红线、分类升级三类。

    - supply: 材料 availability 为 discontinued / restricted（与 scan_supply_risk 同口径，
      此处不展开替代建议；要建议调 scan_supply_risk）。
    - over_cost: 配方预测成本（下限估算）超 cost_limit_per_kg，或单材料价格超
      material_price_limits 中的阈值。两参数均为空 → 不扫此类。
    - redline: 配方命中项目红线（需 session + req.project_id，复用 Phase 1
      check_formulation_redlines；无 session/无项目 → 跳过此类）。
    - classification_upgrade: 当前重分类比调用方传入的基线分类更严重（需 session +
      baseline_classifications；基线缺失的配方跳过）。

    全部 fail-open：单个配方检查失败只记日志，不阻断其余配方。
    """
    from ..domain import knowledge
    from ..pipeline import reconstruct

    triggers: list[dict] = []
    at_risk = {
        name: spec.get("availability")
        for name, spec in knowledge.RAW_MATERIALS.items()
        if spec.get("availability") in ("discontinued", "restricted")
    }
    constraints = None
    if session is not None and req is not None and getattr(req, "project_id", None):
        try:
            from .project_redlines import load_project_constraints

            constraints = load_project_constraints(session, req.project_id)
        except Exception as exc:
            logger.debug("substitution triggers: load constraints failed ({})", exc)
    for label, genome in (genomes or {}).items():
        # supply: 逐槽位
        for i, slot in enumerate(genome.slots):
            if slot.material in at_risk:
                triggers.append({
                    "type": "supply", "formulation": label, "slot_index": i,
                    "material": slot.material, "availability": at_risk[slot.material],
                })
        # 其余三类需要配方实体；失败则跳过该配方（fail-open）
        try:
            form = reconstruct.formulation_from_genome(
                req if req is not None else genome.domain, genome, strict=False)
        except Exception as exc:
            logger.debug("substitution triggers: {} formulation build failed ({})", label, exc)
            continue
        # over_cost
        if cost_limit_per_kg is not None or material_price_limits:
            try:
                cost, unknown = _formulation_cost_per_kg(form, knowledge)
                if cost_limit_per_kg is not None and cost > float(cost_limit_per_kg):
                    triggers.append({
                        "type": "over_cost", "formulation": label, "reason": "formulation_cost",
                        "cost_per_kg": round(cost, 4), "limit": float(cost_limit_per_kg),
                        "cost_unknown_materials": unknown,
                        "detail": f"配方成本估算 {cost:.2f} 元/kg 超上限 {float(cost_limit_per_kg):.2f} 元/kg（无价材料按 0 计，成本为下限）",
                    })
                for ing in getattr(form, "ingredients", None) or []:
                    name = getattr(ing, "name", "") or ""
                    limit = (material_price_limits or {}).get(name)
                    if limit is None:
                        continue
                    price = (knowledge.RAW_MATERIALS.get(name) or {}).get("price_cny_per_kg")
                    if price is not None and float(price) > float(limit):
                        triggers.append({
                            "type": "over_cost", "formulation": label, "reason": "material_price",
                            "material": name, "price_cny_per_kg": float(price), "limit": float(limit),
                            "detail": f"材料 {name} 价格 {float(price):.2f} 元/kg 超阈值 {float(limit):.2f} 元/kg",
                        })
            except Exception as exc:
                logger.debug("substitution triggers: {} cost check failed ({})", label, exc)
        # redline
        if constraints is not None:
            try:
                from .project_redlines import check_formulation_redlines, formulation_to_items

                hits, _ = check_formulation_redlines(
                    session, formulation_to_items(form), constraints,
                    voc_gpl=(getattr(form, "predicted", None) or {}).get("voc_gpl"))
                for h in hits:
                    triggers.append({
                        "type": "redline", "formulation": label, "code": h.code, "message": h.message,
                        "detail": f"命中红线 {h.code}：{h.message}",
                    })
            except Exception as exc:
                logger.debug("substitution triggers: {} redline check failed ({})", label, exc)
        # classification_upgrade
        baseline = (baseline_classifications or {}).get(label)
        if session is not None and baseline:
            try:
                from .plm.ghs.service import build_and_classify
                from .project_redlines import formulation_to_items

                current, _ = build_and_classify(session, formulation_to_items(form))
                current_summary = _classification_summary(current)
                cmp = compare_classifications(baseline, current_summary)
                if cmp["upgraded"]:
                    # P3-1: 子类别变严 / 象形图新增同样触发时，detail 须说清升级原因，
                    # 否则触发只写信号词会让人误以为分类没变。
                    cat_detail = "; ".join(
                        f"{c['h_code']} 类别 {c['category_before']}→{c['category_after']}"
                        for c in _category_changes(baseline, current_summary)
                    )
                    added_pictos = sorted(
                        set(current_summary.get("pictograms") or [])
                        - set(baseline.get("pictograms") or [])
                    )
                    before_summary = {"signal_word": baseline.get("signal_word"),
                                      "h_codes": sorted(baseline.get("h_codes") or []),
                                      "pictograms": sorted(baseline.get("pictograms") or [])}
                    if baseline.get("categories"):
                        before_summary["categories"] = dict(baseline.get("categories") or {})
                    triggers.append({
                        "type": "classification_upgrade", "formulation": label,
                        "classification_before": before_summary,
                        "classification_after": current_summary,
                        "added_h_codes": cmp["added_h_codes"],
                        "detail": f"分类升级：信号词 {baseline.get('signal_word')} → {current.signal_word}"
                                  + (f"，新增危害 {', '.join(cmp['added_h_codes'])}" if cmp["added_h_codes"] else "")
                                  + (f"，子类别变严 {cat_detail}" if cat_detail else "")
                                  + (f"，新增象形图 {', '.join(added_pictos)}" if added_pictos else ""),
                    })
            except Exception as exc:
                logger.debug("substitution triggers: {} classification check failed ({})", label, exc)
    counts: dict[str, int] = {}
    for t in triggers:
        counts[t["type"]] = counts.get(t["type"], 0) + 1
    return {"triggers": triggers, "counts": counts}


def scan_supply_risk(
    genomes: dict[str, FormulationGenome] | None = None,
    req: Requirement | None = None,
) -> dict:
    """Which materials are at supply risk, and which formulations they hit.

    This is the trigger the availability field exists for: mark a material
    discontinued and every affected formulation surfaces with replacements
    already ranked, instead of waiting for someone to notice at reorder time.
    """
    from ..domain import knowledge

    at_risk = {
        name: spec.get("availability")
        for name, spec in knowledge.RAW_MATERIALS.items()
        if spec.get("availability") in ("discontinued", "restricted")
    }
    affected: list[dict] = []
    for label, genome in (genomes or {}).items():
        hits = [
            {"slot_index": i, "material": s.material, "availability": at_risk[s.material]}
            for i, s in enumerate(genome.slots)
            if s.material in at_risk
        ]
        if not hits:
            continue
        entry = {"formulation": label, "affected_slots": hits, "suggestions": {}}
        for hit in hits:
            try:
                report = find_substitutes(
                    genome, hit["slot_index"], req, limit=3, include_external=False
                )
                entry["suggestions"][hit["material"]] = [
                    {
                        "material": c["material"],
                        "structural_score": c["structural_score"],
                        "feasible": c["feasible"],
                        "delta_confidence": c["delta_confidence"],
                    }
                    for c in report["candidates"]
                ]
            except Exception as exc:
                logger.debug("supply scan: {} failed ({})", hit["material"], exc)
        affected.append(entry)
    return {"at_risk": at_risk, "affected": affected}
