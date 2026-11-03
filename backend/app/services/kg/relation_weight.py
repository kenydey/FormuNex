"""WeKnora 第一批 2.5 A 线：关系权重纯函数（查询时算，不入库）。

权重 = PMI 0.6 + 强度 0.4（WeKnora 生产路径公式，归一化到 0–1；
WeKnora 的 1–10 只作展示换算 ×10，不进排序）。

PMI 定义（写死，不留歧义）：以 chunk 为共现单位，
``PMI(a, b) = log( P(a, b) / (P(a) * P(b)) )``，其中
``P(a, b) = (cooc + 1) / total``，``P(a) = (count_a + 1) / total``
（分子分母各 +1 平滑，防零频与单次共现虚高）；``pmi_norm``
为**本次候选集内**的 max 归一化（max ≤ 0 时为 0），不建全局
统计表。强度 = 0.5 × 候选集内归一化共现次数 + 0.5 × 抽取置信度均值。

本模块与 Neo4j 的 ``weight``（写入时带的置信度衍生值）来源不同、
语义不同，互不引用。
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

DEFAULT_PMI_WEIGHT = 0.6
DEFAULT_STRENGTH_WEIGHT = 0.4
DEFAULT_DECAY = 0.5


def resolve_coefficients(pmi_w: float, strength_w: float) -> tuple[float, float]:
    """两系数之和须为 1；不为 1 时记 warning 并回默认值（不静默归一化）。"""
    try:
        if abs(float(pmi_w) + float(strength_w) - 1.0) <= 1e-9:
            return float(pmi_w), float(strength_w)
    except (TypeError, ValueError):
        pass
    logger.warning(
        "kg relation weight coefficients pmi=%r strength=%r do not sum to 1; "
        "falling back to defaults %s/%s",
        pmi_w, strength_w, DEFAULT_PMI_WEIGHT, DEFAULT_STRENGTH_WEIGHT,
    )
    return DEFAULT_PMI_WEIGHT, DEFAULT_STRENGTH_WEIGHT


def relation_weight(
    *,
    pmi_norm: float,
    strength: float,
    pmi_w: float = DEFAULT_PMI_WEIGHT,
    strength_w: float = DEFAULT_STRENGTH_WEIGHT,
) -> float:
    """返回 0–1 的关系权重（钳制到 [0, 1]，防负 PMI 溢出下界）。"""
    w = float(pmi_w) * float(pmi_norm) + float(strength_w) * float(strength)
    return max(0.0, min(1.0, w))


def decayed_weight(weight: float, *, hops: int, decay: float = DEFAULT_DECAY) -> float:
    """间接衰减：1 跳不衰减，n 跳 × decay ** (n - 1)。"""
    if hops <= 1:
        return float(weight)
    return float(weight) * (float(decay) ** (hops - 1))


def compute_pair_weights(
    pairs: list[tuple[str, str, float]],
    entity_chunks: dict[str, set[str]],
    total_chunks: int,
    *,
    pmi_w: float = DEFAULT_PMI_WEIGHT,
    strength_w: float = DEFAULT_STRENGTH_WEIGHT,
) -> dict[tuple[str, str], float]:
    """对候选集内的关系对批量算权重。

    ``pairs`` 为 (src_entity_id, dst_entity_id, 抽取置信度均值)；
    ``entity_chunks`` 为 entity_id → 提及它的 chunk_id 集合；
    ``total_chunks`` 为该集合涉及的去重 chunk 总数（0 时全部回落 0）。
    返回 {(a, b): weight}，键按输入顺序（无向对不归一化方向，
    调用方按视图的 src/dst 取键）。
    """
    pmi_w, strength_w = resolve_coefficients(pmi_w, strength_w)
    if total_chunks <= 0:
        return {(a, b): max(0.0, min(1.0, conf)) for a, b, conf in pairs}
    raw: list[tuple[tuple[str, str], float, float, float]] = []
    for a, b, conf in pairs:
        ca = entity_chunks.get(a) or set()
        cb = entity_chunks.get(b) or set()
        cooc = len(ca & cb)
        p_ab = (cooc + 1) / total_chunks
        p_a = (len(ca) + 1) / total_chunks
        p_b = (len(cb) + 1) / total_chunks
        pmi = math.log(p_ab / (p_a * p_b)) if p_a > 0 and p_b > 0 else 0.0
        raw.append(((a, b), pmi, float(cooc), float(conf)))
    max_pmi = max((pmi for _, pmi, _, _ in raw), default=0.0)
    max_cooc = max((cooc for _, _, cooc, _ in raw), default=0.0)
    out: dict[tuple[str, str], float] = {}
    for key, pmi, cooc, conf in raw:
        pmi_norm = (pmi / max_pmi) if max_pmi > 0 else 0.0
        cooc_norm = (cooc / max_cooc) if max_cooc > 0 else 0.0
        strength = 0.5 * cooc_norm + 0.5 * max(0.0, min(1.0, conf))
        out[key] = relation_weight(
            pmi_norm=pmi_norm, strength=strength, pmi_w=pmi_w, strength_w=strength_w
        )
    return out


def mention_chunks(rows) -> tuple[dict[str, set[str]], int]:
    """KGMention 行 → (entity_id → chunk_id 集合, 去重 chunk 总数)。"""
    entity_chunks: dict[str, set[str]] = {}
    all_chunks: set[str] = set()
    for row in rows or []:
        entity_chunks.setdefault(row.entity_id, set()).add(row.chunk_id)
        all_chunks.add(row.chunk_id)
    return entity_chunks, len(all_chunks)
