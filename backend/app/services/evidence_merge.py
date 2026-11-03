"""WeKnora 第一批 2.3：查询时证据合并管线（邻块扩展 + 再去重）。

纯函数层（除 ``neighbour_lookup`` 回调外不读 DB、不读 settings）：
输入是已按 relevance 排好的 Evidence 列表，输出是合并后的列表，
顺序 = 首次出现顺序，分数口径不变（合并项 relevance 取最大值）。

与 WeKnora ``chat_pipeline/merge.go`` 8 步的对齐（见子方案）：
① 输入直接取用；② ID + 签名去重；③ 历史引用注入不做（2.1 已覆盖）；
④ 父块还原不做（呈现单位本就是父块）；⑤ 按源分组、顺序合并；
⑥ FAQ 填充不做（无 FAQ 类型）；⑦ 短上下文邻块扩展；⑦.5 扩展后再合并；
⑧ 最终去重（ID + 签名 + 部分重叠）。

作用域说明：签名 / 包含 / Jaccard 去重只在**同一 source 内**做——
跨源相同文本是两个独立引用，丢掉会损失引用完整性；只有 identifier
去重是全局的（kb 路径的 identifier 本身全局唯一）。
"""

from __future__ import annotations

import logging
import re

from ..domain.pure import chunk_dedup_key

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^kb:([^#]+)#c(\d+)$")


def inherit_image_evidence(kept, dropped):
    """WeKnora 第一批 2.4：图像证据继承（纯函数，不动入参）。

    丢弃方不是图像证据（image_refs 为空且 image_vector_match 为 False）
    → 保留方原样返回；否则保留方并集丢弃方的 image_refs（去重）、
    置 image_vector_match=True；kept_by 已有值的不覆盖。
    """
    dropped_refs = list(getattr(dropped, "image_refs", None) or [])
    dropped_match = bool(getattr(dropped, "image_vector_match", False))
    if not dropped_refs and not dropped_match:
        return kept
    refs = list(getattr(kept, "image_refs", None) or [])
    for r in dropped_refs:
        if r and r not in refs:
            refs.append(r)
    update = {"image_refs": refs, "image_vector_match": True}
    if getattr(kept, "kept_by", None) is None:
        update["kept_by"] = "image_vector"
    return kept.model_copy(update=update)


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def _tokens(text: str) -> set[str]:
    try:
        from .text_tokenize import tokenize

        return {t for t in tokenize(text or "") if t}
    except Exception:  # noqa: BLE001 - fail-open: tokenize failure = no overlap
        return set((text or "").split())


def _ident_of(ev) -> tuple[str | None, int | None]:
    ident = ev if isinstance(ev, str) else (getattr(ev, "identifier", "") or "")
    m = _IDENT_RE.match(ident)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _scope_of(ev) -> str:
    """去重作用域：kb 项按 source_id（不是 source 字段——kb 项的
    source 都是 'kb'，按它分组会把不同文档当成同一文档），
    非 kb 项按 identifier 本身（只对同一文档内的 chunk 去重，
    不同专利 / 文献的相同文本是独立引用，不丢）。"""
    src, _ = _ident_of(ev)
    if src is not None:
        return f"kb:{src}"
    return getattr(ev, "identifier", "") or getattr(ev, "source", "") or ""


def _join_texts(a: str, b: str, overlap_chars: int) -> str:
    """JoinChunkContent 三规则：包含折叠、前后缀重叠消除、大块护栏由调用方管。"""
    a_n, b_n = _norm(a), _norm(b)
    if not a_n:
        return b_n
    if not b_n:
        return a_n
    if b_n in a_n:
        return a_n
    if a_n in b_n:
        return b_n
    cap = max(2, min(int(overlap_chars), len(a_n), len(b_n)))
    for k in range(cap, 1, -1):
        if a_n.endswith(b_n[:k]):
            return a_n + b_n[k:]
    return a_n + "\n" + b_n


def _merged_copy(kept, other, text: str, *, inherit_images: bool = True):
    merged_from = list(getattr(kept, "merged_from", None) or [])
    for ident in [getattr(other, "identifier", "")] + list(
        getattr(other, "merged_from", None) or []
    ):
        if ident and ident not in merged_from:
            merged_from.append(ident)
    merged = kept.model_copy(
        update={
            "snippet": text,
            "relevance": max(
                float(getattr(kept, "relevance", 0.0) or 0.0),
                float(getattr(other, "relevance", 0.0) or 0.0),
            ),
            "merged_from": merged_from,
        }
    )
    # 2.4：被并入方若为图像证据，保留方继承其图像引用。
    if inherit_images:
        merged = inherit_image_evidence(merged, other)
    return merged


def _dedup_and_merge(
    items: list, *, max_chars: int, overlap_chars: int, inherit_images: bool = True
) -> list:
    """步骤 ② + ⑤：ID 去重、签名去重、同源连续 ord 合并。

    每次丢弃（ID / 签名重复、被并入）时，若 inherit_images 为真，
    保留方继承丢弃方的图像证据（2.4，与 2.3 文本合并并行不冲突）。
    """
    kept_by_key: dict[str, int] = {}
    out: list = []
    for ev in items:
        key = getattr(ev, "identifier", "") or (
            f"title:{getattr(ev, 'title', '')}|page:{getattr(ev, 'page', '')}"
        )
        if key in kept_by_key:
            if inherit_images:
                idx = kept_by_key[key]
                out[idx] = inherit_image_evidence(out[idx], ev)
            continue
        kept_by_key[key] = len(out)
        out.append(ev)

    kept_by_sig: dict[tuple[str, str], int] = {}
    deduped: list = []
    for ev in out:
        sig = (
            _scope_of(ev),
            chunk_dedup_key(
                getattr(ev, "snippet", ""), getattr(ev, "title", ""), getattr(ev, "page", None)
            ),
        )
        if sig in kept_by_sig:
            if inherit_images:
                idx = kept_by_sig[sig]
                deduped[idx] = inherit_image_evidence(deduped[idx], ev)
            continue
        kept_by_sig[sig] = len(deduped)
        deduped.append(ev)

    # 按源分组：kb 项按 ord 排序合并连续项；非 kb 项保持原顺序、不合并。
    # 非 kb 项与 kb 组合并项按首次出现位置交错，而非把所有 kb 项堆到末尾。
    kb_groups: dict[str, list] = {}
    kb_order: list[str] = []
    first_pos: dict[str, int] = {}
    placed: dict[int, list] = {}
    for pos, ev in enumerate(deduped):
        src, ord_no = _ident_of(ev)
        if src is None:
            placed.setdefault(pos, []).append(ev)
            continue
        if src not in kb_groups:
            kb_groups[src] = []
            kb_order.append(src)
            first_pos[src] = pos
        kb_groups[src].append((ord_no, ev))
    for src in kb_order:
        group = sorted(kb_groups[src], key=lambda t: t[0])
        cur = None
        cur_last_ord: int | None = None
        group_out: list = []
        for ord_no, ev in group:
            if (
                cur is not None
                and cur_last_ord is not None
                and ord_no == cur_last_ord + 1
            ):
                text = _join_texts(cur.snippet, ev.snippet, overlap_chars)
                if len(text) <= max_chars:
                    cur = _merged_copy(cur, ev, text, inherit_images=inherit_images)
                    cur_last_ord = ord_no
                    continue
            if cur is not None:
                group_out.append(cur)
            cur = ev
            cur_last_ord = ord_no
        if cur is not None:
            group_out.append(cur)
        placed.setdefault(first_pos[src], []).extend(group_out)
    result: list = []
    for pos in sorted(placed):
        result.extend(placed[pos])
    return result


def merge_evidence(
    evidence: list,
    *,
    neighbour_lookup=None,
    short_chars: int = 400,
    max_chars: int = 4000,
    overlap_chars: int = 500,
    inherit_images: bool = True,
) -> list:
    """合并证据列表。``neighbour_lookup(source_id, ord)`` 返回
    ``(snippet_text, identifier[, is_image])`` 或 None（查不到 / 已删时）；
    回调抛错时该侧扩展跳过，不阻断整条合并。``inherit_images=False``
    时 2.4 继承关闭（开关回归用），文本合并本身不受影响。"""
    if not evidence:
        return []
    items = _dedup_and_merge(
        evidence, max_chars=max_chars, overlap_chars=overlap_chars,
        inherit_images=inherit_images,
    )

    if neighbour_lookup is not None:
        expanded: list = []
        consumed: set[str] = set()
        for ev in items:
            src, ord_no = _ident_of(ev)
            if src is None or ord_no is None or len(_norm(ev.snippet)) >= short_chars:
                expanded.append(ev)
                continue
            # 合并后的条目覆盖一个 ord 区间：邻块取区间两端外侧，
            # 而不是只取主 identifier 的两侧（否则合并区间的另一端漏扩）。
            span_ords = {ord_no}
            for ident in getattr(ev, "merged_from", None) or []:
                s2, o2 = _ident_of(ident)
                if s2 == src and o2 is not None:
                    span_ords.add(o2)
            cur = ev
            cur_ids = {getattr(ev, "identifier", "")} | set(
                getattr(ev, "merged_from", None) or []
            )
            for side_ord in (min(span_ords) - 1, max(span_ords) + 1):
                if side_ord < 0 or side_ord in span_ords:
                    continue
                try:
                    found = neighbour_lookup(src, side_ord)
                except Exception:  # noqa: BLE001 - fail-open: that side is skipped
                    logger.debug("evidence_merge neighbour lookup failed", exc_info=True)
                    continue
                if not found:
                    continue
                nb_text, nb_ident = found[0], found[1]
                nb_is_image = len(found) > 2 and bool(found[2])
                if nb_ident in cur_ids or not _norm(nb_text):
                    continue
                if side_ord < min(span_ords):
                    text = _join_texts(nb_text, cur.snippet, overlap_chars)
                else:
                    text = _join_texts(cur.snippet, nb_text, overlap_chars)
                if len(text) > max_chars:
                    continue
                merged_from = list(getattr(cur, "merged_from", None) or [])
                if nb_ident not in merged_from:
                    merged_from.append(nb_ident)
                cur = cur.model_copy(
                    update={"snippet": text, "merged_from": merged_from}
                )
                # 2.4：邻块若为 figure / image 块，扩展方继承其图像引用。
                if inherit_images and nb_is_image:
                    from types import SimpleNamespace as _NS

                    cur = inherit_image_evidence(
                        cur,
                        _NS(image_refs=[nb_ident], image_vector_match=True, kept_by=None),
                    )
                cur_ids.add(nb_ident)
                consumed.add(nb_ident)
            expanded.append(cur)
        items = [ev for ev in expanded if (getattr(ev, "identifier", "") or "") not in consumed]
        # 扩展后再合并（⑦.5）：不再二次扩展。
        items = _dedup_and_merge(
            items, max_chars=max_chars, overlap_chars=overlap_chars,
            inherit_images=inherit_images,
        )

    # 最终去重（⑧）：同源内子串包含丢短者、Jaccard ≥ 0.85 丢低 relevance 者；
    # 被丢弃方若为图像证据，保留方继承（2.4）。
    result: list = []
    for ev in items:
        ev_norm = _norm(ev.snippet)
        ev_toks = _tokens(ev_norm)
        drop = False
        for idx, kept in enumerate(result):
            if _scope_of(kept) != _scope_of(ev):
                continue
            kept_norm = _norm(kept.snippet)
            if ev_norm and ev_norm in kept_norm:
                if inherit_images:
                    result[idx] = inherit_image_evidence(kept, ev)
                drop = True
                break
            if kept_norm and kept_norm in ev_norm:
                ev = inherit_image_evidence(ev, kept) if inherit_images else ev
                del result[idx]
                break
            if ev_toks and _tokens(kept_norm):
                inter = len(ev_toks & _tokens(kept_norm))
                union = len(ev_toks | _tokens(kept_norm))
                if union and inter / union >= 0.85:
                    if float(getattr(ev, "relevance", 0.0) or 0.0) > float(
                        getattr(kept, "relevance", 0.0) or 0.0
                    ):
                        ev = inherit_image_evidence(ev, kept) if inherit_images else ev
                        del result[idx]
                    else:
                        if inherit_images:
                            result[idx] = inherit_image_evidence(kept, ev)
                        drop = True
                    break
        if not drop:
            result.append(ev)
    return result
