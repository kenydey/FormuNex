"""KG-R2 — graph traversal over semantic kb_entity_links."""
from __future__ import annotations

from collections import deque

from ...db.entity_store import SEMANTIC_LINK_TYPES, get_entity_store
from ...db.models import KGEntityLink
from ...domain.kg_schemas import (
    KGPathResponse,
    KGPathStep,
    KGRelationView,
    KGSubstituteCandidate,
    KGSubstituteDiscoverResponse,
    RelationEvidence,
    RelationType,
)


def _entity_display_name(entity_id: str, cache: dict[str, str]) -> str:
    if entity_id in cache:
        return cache[entity_id]
    row = get_entity_store().get_entity(entity_id)
    name = (row.canonical_name if row else entity_id) or entity_id
    if row and row.zh_name:
        name = f"{row.zh_name} ({name})"
    # 2.5 投影截断：展示名超 120 字符时截断（取 heading_path 列宽先例，
    # 不取 WeKnora 的 513）；只影响展示字段，不影响 entity_id 与查询。
    name = name[:120]
    cache[entity_id] = name
    return name


def _weight_config() -> tuple[bool, float, float, float]:
    """2.5 权重开关与系数（系数和校验在 relation_weight 内双重把关，
    防止 settings 被直接改写绕过 config 校验）。"""
    try:
        from ...config import get_settings

        settings = get_settings()
        return (
            bool(settings.kg_relation_weight_enabled),
            float(settings.kg_relation_pmi_weight),
            float(settings.kg_relation_strength_weight),
            float(settings.kg_relation_decay),
        )
    except Exception:  # noqa: BLE001 - fail-open: weight off
        return False, 0.6, 0.4, 0.5


def _pair_weights_for_views(views: list[KGRelationView]) -> dict[tuple[str, str], float]:
    """对一组视图批量算权重。mention 统计缺失 / 异常时回落 confidence。"""
    from .relation_weight import compute_pair_weights, mention_chunks

    pairs: list[tuple[str, str, float]] = []
    for v in views:
        confs = [float(e.confidence) for e in v.evidence if e.confidence is not None]
        conf = sum(confs) / len(confs) if confs else float(v.confidence or 0.5)
        pairs.append((v.source_entity_id, v.target_entity_id, conf))
    try:
        store = get_entity_store()
        ids = sorted({e for a, b, _ in pairs for e in (a, b)})
        rows, _total, _trunc = store.fetch_mentions_for_entities(ids, scan_limit=500)
        entity_chunks, total_chunks = mention_chunks(rows)
        return compute_pair_weights(pairs, entity_chunks, total_chunks)
    except Exception:  # noqa: BLE001 - per-relation fallback below
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "relation weight fallback to confidence", exc_info=True
        )
        return {(a, b): max(0.0, min(1.0, conf)) for a, b, conf in pairs}


def _annotate_views(views: list[KGRelationView]) -> list[KGRelationView]:
    enabled, _p, _s, _d = _weight_config()
    if not enabled:
        return views
    weights = _pair_weights_for_views(views)
    out = []
    for v in views:
        w = weights.get((v.source_entity_id, v.target_entity_id))
        out.append(v.model_copy(update={"weight": w}))
    return out


def link_to_view(link: KGEntityLink, *, name_cache: dict[str, str] | None = None) -> KGRelationView:
    evidence = [
        RelationEvidence(
            source_id=ref.get("source_id", ""),
            chunk_id=ref.get("chunk_id"),
            sentence=ref.get("sentence", ""),
            confidence=float(ref.get("confidence", link.confidence or 0.5)),
            extraction_method=ref.get("extraction_method", link.extraction_method or "rule"),
        )
        for ref in (link.evidence_refs or [])
    ]
    rel_type = link.link_type
    try:
        relation_type = RelationType(rel_type)
    except ValueError:
        relation_type = RelationType.SUBSTITUTES
    return KGRelationView(
        id=link.id,
        source_entity_id=link.src_entity_id,
        target_entity_id=link.dst_entity_id,
        relation_type=relation_type,
        confidence=float(link.confidence or 0.5),
        evidence=evidence,
        metadata=dict(link.metadata_json or {}),
        is_valid=bool(link.is_valid),
        extraction_method=link.extraction_method or "rule",
    )


def get_entity_relations(
    entity_id: str,
    *,
    direction: str = "both",
    link_types: list[str] | None = None,
    extraction_method: str | None = None,
    limit: int = 50,
) -> list[KGRelationView]:
    store = get_entity_store()
    types = link_types or list(SEMANTIC_LINK_TYPES)
    links = store.get_links_for_entity(
        entity_id,
        direction=direction,
        link_types=types,
        limit=limit if not extraction_method else limit * 3,
    )
    cache: dict[str, str] = {}
    views = [link_to_view(link, name_cache=cache) for link in links]
    if extraction_method:
        wanted = extraction_method.strip().lower()
        views = [v for v in views if (v.extraction_method or "").lower() == wanted or any((e.extraction_method or "").lower() == wanted for e in v.evidence)]
        views = views[:limit]
    # 2.5：取数逻辑不动，取后按 weight 降序稳定排序（同分保原序）。
    enabled, _p, _s, _d = _weight_config()
    if enabled:
        views = _annotate_views(views)
        views.sort(key=lambda v: -(v.weight if v.weight is not None else v.confidence))
    return views


def find_path(
    src_entity_id: str,
    dst_entity_id: str,
    *,
    max_depth: int = 4,
    link_types: list[str] | None = None,
) -> KGPathResponse:
    if src_entity_id == dst_entity_id:
        cache: dict[str, str] = {}
        return KGPathResponse(
            src_entity_id=src_entity_id,
            dst_entity_id=dst_entity_id,
            found=True,
            hops=0,
            steps=[],
        )

    types = set(link_types or SEMANTIC_LINK_TYPES)
    store = get_entity_store()
    cache: dict[str, str] = {}

    # BFS: state = (current_entity_id, path_of_steps)
    queue: deque[tuple[str, list[KGPathStep]]] = deque([(src_entity_id, [])])
    visited: set[str] = {src_entity_id}

    while queue:
        current, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        links = store.get_links_for_entity(current, direction="both", link_types=list(types), limit=80)
        for link in links:
            rel_view = link_to_view(link, name_cache=cache)
            neighbors: list[tuple[str, bool]] = []
            if link.src_entity_id == current and link.dst_entity_id not in visited:
                neighbors.append((link.dst_entity_id, True))
            if link.dst_entity_id == current and link.src_entity_id not in visited:
                neighbors.append((link.src_entity_id, False))

            for neighbor, forward in neighbors:
                if neighbor in visited:
                    continue
                step = KGPathStep(
                    relation=rel_view,
                    entity_id=neighbor,
                    entity_name=_entity_display_name(neighbor, cache),
                )
                new_path = path + [step]
                if neighbor == dst_entity_id:
                    # 2.5：BFS 首达语义不变，只在返回的每步上标注 weight
                    # （第 i 跳按位置衰减：step 1 不衰减、step 2 ×decay……），
                    # 不参与“找不找得到”的判定。
                    enabled, _p, _s, decay = _weight_config()
                    if enabled:
                        from .relation_weight import decayed_weight

                        weights = _pair_weights_for_views(
                            [s.relation for s in new_path]
                        )
                        annotated = []
                        for i, s in enumerate(new_path, start=1):
                            rel = s.relation
                            w = weights.get(
                                (rel.source_entity_id, rel.target_entity_id),
                                float(rel.confidence or 0.5),
                            )
                            annotated.append(
                                s.model_copy(
                                    update={
                                        "relation": rel.model_copy(
                                            update={
                                                "weight": decayed_weight(
                                                    w, hops=i, decay=decay
                                                )
                                            }
                                        )
                                    }
                                )
                            )
                        new_path = annotated
                    return KGPathResponse(
                        src_entity_id=src_entity_id,
                        dst_entity_id=dst_entity_id,
                        found=True,
                        hops=len(new_path),
                        steps=new_path,
                    )
                visited.add(neighbor)
                queue.append((neighbor, new_path))

    return KGPathResponse(
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        found=False,
        hops=0,
        steps=[],
    )


def discover_substitutes(
    entity_id: str,
    *,
    limit: int = 10,
    max_depth: int = 2,
) -> KGSubstituteDiscoverResponse:
    store = get_entity_store()
    cache: dict[str, str] = {}
    entity_name = _entity_display_name(entity_id, cache)
    candidates: list[KGSubstituteCandidate] = []
    seen: set[str] = {entity_id}

    # v10: pre-compute contradiction flags per candidate target
    _contra_map: dict[str, str] = {}
    try:
        from ...config import get_settings
        from .contradiction import detect_contradictions

        if get_settings().kg_contradiction_demote:
            _resp = detect_contradictions(entity_id)
            for _m in _resp.contradictions:
                _contra_map[_m.target_entity_id] = _m.contradiction_type
    except Exception as _exc:  # pragma: no cover - defensive
        import logging as _logging
        _logging.getLogger(__name__).warning("discover_contradiction precompute failed: %s", _exc)

    def _add_candidate(sub_id: str, rel: KGRelationView, path: list[KGPathStep]) -> None:
        if sub_id in seen:
            return
        seen.add(sub_id)
        contra = _contra_map.get(sub_id, "")
        candidates.append(
            KGSubstituteCandidate(
                entity_id=sub_id,
                entity_name=_entity_display_name(sub_id, cache),
                relation_type=RelationType.SUBSTITUTES,
                confidence=rel.confidence,
                hops=len(path),
                path=path,
                contradiction_flag=bool(contra),
                contradiction_detail=contra,
            )
        )

    # Direct substitutes: outgoing substitutes + incoming (reverse)
    direct = store.get_links_for_entity(
        entity_id,
        direction="both",
        link_types=["substitutes"],
        limit=limit * 2,
    )
    for link in direct:
        rel = link_to_view(link, name_cache=cache)
        if link.link_type != "substitutes":
            continue
        if link.src_entity_id == entity_id:
            sub_id = link.dst_entity_id
        else:
            sub_id = link.src_entity_id
        step = KGPathStep(
            relation=rel,
            entity_id=sub_id,
            entity_name=_entity_display_name(sub_id, cache),
        )
        _add_candidate(sub_id, rel, [step])
        if len(candidates) >= limit:
            break

    # One more substitutes hop: a substitute of a substitute is a candidate.
    #
    # This used to request ``["substitutes", "synergizes"]`` and then discard
    # every non-substitutes row. That was not merely dead code: the store
    # applies ``ORDER BY confidence DESC LIMIT 20`` *before* this filter runs,
    # so high-confidence synergy edges consumed the budget and pushed real
    # substitutes edges out — an entity with 20 strong synergy links returned
    # no second-hop candidates at all.
    if len(candidates) < limit and max_depth > 1:
        frontier = list(candidates)
        for cand in frontier:
            if len(candidates) >= limit:
                break
            bridge_links = store.get_links_for_entity(
                cand.entity_id,
                direction="both",
                link_types=["substitutes"],
                limit=20,
            )
            for link in bridge_links:
                if link.link_type != "substitutes":
                    continue
                if link.src_entity_id == cand.entity_id:
                    sub_id = link.dst_entity_id
                else:
                    sub_id = link.src_entity_id
                if sub_id in seen:
                    continue
                rel = link_to_view(link, name_cache=cache)
                path = cand.path + [
                    KGPathStep(
                        relation=rel,
                        entity_id=sub_id,
                        entity_name=_entity_display_name(sub_id, cache),
                    )
                ]
                _add_candidate(sub_id, rel, path)
                if len(candidates) >= limit:
                    break

    # 2.5：候选权重 = 末跳关系权重经 hops 衰减；排序在原有 measured /
    # 矛盾分层内加入 weight（不取代这两层语义，只在层内以 weight 优先）。
    _w_enabled, _p, _s, _decay = _weight_config()
    if _w_enabled:
        from .relation_weight import decayed_weight

        rel_views = []
        for c in candidates:
            rel = c.path[-1].relation if c.path else None
            rel_views.append(rel)
        weights = _pair_weights_for_views([r for r in rel_views if r is not None])
        annotated = []
        for c, rel in zip(candidates, rel_views):
            if rel is None:
                annotated.append(c)
                continue
            w = weights.get(
                (rel.source_entity_id, rel.target_entity_id),
                float(rel.confidence or 0.5),
            )
            annotated.append(
                c.model_copy(
                    update={"weight": decayed_weight(w, hops=c.hops, decay=_decay)}
                )
            )
        candidates = annotated

    def _wkey(c) -> float:
        return -(c.weight if c.weight is not None else c.confidence)

    candidates.sort(key=lambda c: (_wkey(c), -c.confidence, c.hops))
    # measured 优先：实测边涉及的实体前置
    _measured: set[str] = set()
    try:
        for link in store.get_links_for_entity(entity_id, direction="both", link_types=["substitutes", "inhibits", "synergizes"], limit=200):
            if getattr(link, "extraction_method", None) == "measured":
                _measured.add(link.src_entity_id)
                _measured.add(link.dst_entity_id)
        if _measured:
            candidates.sort(key=lambda c: (0 if c.entity_id in _measured else 1, _wkey(c), -c.confidence, c.hops))
    except Exception:
        pass
    # v10: 矛盾候选置后（被实测反驳的替代物排在末尾），除非实测明确好
    if _contra_map:
        candidates.sort(
            key=lambda c: (
                1 if c.contradiction_flag else 0,
                0 if c.entity_id in _measured else 1,
                _wkey(c),
                -c.confidence,
                c.hops,
            )
        )
    return KGSubstituteDiscoverResponse(
        query_entity_id=entity_id,
        query_entity_name=entity_name,
        substitutes=candidates[:limit],
    )
