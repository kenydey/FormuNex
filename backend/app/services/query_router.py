"""融合升级批 4：确定性查询路由器 v1（显式路由，不用 LLM 路由器）。

一次问答只走该走的管道，不再是无条件顺序叠加。路由器只决定管道，
不改变各管道内部行为：Wiki 混入权重（0.7）与纯 Wiki 支撑置信度封顶
（0.6）不变；text2sql 分类本身不动；KG/hybrid 分支不动；deep 臂只调
已有的 ``two_stage_retrieve(deep=None)`` 自动模式（分类器 + 页数门 +
批 3-2 启发式升级都在其内部生效），不另造触发逻辑。

复用三个已有信号，不新发明分类器：
  - ``text2sql.classify_intent``：structured / hybrid / unstructured；
  - ``two_stage.classify_pageindex_query``：萃取 / 深度类；
  - Wiki 命中：管线自身的 FTS-first 融合 ``_wiki_blend_hits``（FTS 在先，
    为空时回退 keyword + embedded），再按稳定概念页 kind 过滤。
    注意：纯 FTS（``_fts_blend_hits``）对完整问句实测恒为 0 命中
    （``_match_query`` 把问句每个 CJK 字都 AND 进 MATCH，页正文须含问句
    全部单字才命中；开发库 25 题实测 0/25），只用 FTS 会使 wiki_first
    臂永不触发；故信号用管线已在用的融合命中，FTS 仍是其中首要来源。

路由表（优先级即下列顺序；显式 deep 最高优先，见 :func:`route_query`）：
  1. 客户端显式 ``deep=true`` → deep（不经路由，见调用方）；
  2. 结构化信号（intent 为 structured / hybrid）→ structured
     （text2sql hybrid；``hybrid_answer`` 本就无条件调用，路由只定主管道）；
  3. 萃取 / 深度类（``classify_pageindex_query`` 放行）→ deep
     （PageIndex Stage 2 自动模式）；
  4. 稳定概念命中 Wiki 页 → wiki_first（blend 走已有的 wiki_first
     模式 k=8 前置；权重与封顶不变）；
  5. 其余 → hybrid（hybrid RAG 现状管线）。

优先级说明（2026-11-23 实测后定）：structured 与 deep 在开发库问题集
上重叠 4/5（盐雾/对比/组成等词同时触发两个分类器），wiki 与 deep 重叠
1 题；三路都按“首要需求”排——结构化问句首要需求是实验库数据，深度
问句首要需求是长文导航，概念问句才首要需求 Wiki 页。Wiki blend 与
``hybrid_answer`` 在现状管线里本就无条件跑，故 wiki_first / structured
排后损失的只是 k=8 前置或 Stage 2 附加证据，不丢主管道；反之 wiki_first
优先会压掉 deep 臂（唯一新增检索行为）。金标 250 题上该优先级分布为
hybrid 217 / deep 31 / structured 2 / wiki_first 0。

所有信号 fail-open：任一信号抛错即视为无该信号；路由本身永不抛错，
失败时回退 hybrid。开关 ``query_router_enabled``（默认关）见 config。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: 路由结果。``structured`` 即方案所称 text2sql hybrid 臂。
Route = Literal["wiki_first", "deep", "structured", "hybrid"]

#: 确定性编译生成的 Wiki 页 kind（wiki/compile.py 的五类页）。只有命中
#: 这类页才算“稳定概念页”；STORM 报告 / 主题页等非编译页不在此列——
#: 它们的影子 chunk 会因通用词（如 composition / curing）命中英文问句，
#: 实测曾使 3/25 开发库问题误命中。
STABLE_WIKI_KINDS = ("material", "chemical", "system", "mechanism", "pitfall")

#: Wiki 融合命中取的候选数（与 balanced 模式默认 k=5 一致）。
WIKI_BLEND_K = 5


@dataclass
class RouteDecision:
    """一次路由决策：路由 + 决策依据（供日志 / 抽查表 / 探针复核）。"""

    route: Route
    reason: str
    explicit_deep: bool = False
    intent_route: str = "unstructured"
    intent_reason: str = ""
    pageindex_allowed: bool = False
    pageindex_reason: str = ""
    wiki_stable_hit: bool = False
    wiki_stable_pages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "reason": self.reason,
            "explicit_deep": self.explicit_deep,
            "intent_route": self.intent_route,
            "intent_reason": self.intent_reason,
            "pageindex_allowed": self.pageindex_allowed,
            "pageindex_reason": self.pageindex_reason,
            "wiki_stable_hit": self.wiki_stable_hit,
            "wiki_stable_pages": list(self.wiki_stable_pages),
        }


def _stable_wiki_paths() -> set[str]:
    """wiki_pages 中稳定概念页的 path 集合（一次 DB 查询，不读 markdown）。

    查不到（库未初始化 / 表缺失）即返回空集——信号记为无命中，fail-open。
    ``list_pages`` 上限 500 行：页数超过时只认最近更新的 500 页，漏认方向
    是回退 hybrid，不会误路由。
    """
    try:
        from ..db.wiki_store import get_wiki_store

        rows = get_wiki_store().list_pages(limit=500)
    except Exception as exc:  # noqa: BLE001 - 路由信号缺失即无命中
        logger.debug("query router: stable wiki pages unavailable: %s", exc)
        return set()
    return {(r.path or "") for r in rows if (r.kind or "") in STABLE_WIKI_KINDS}


def _wiki_stable_pages(question: str, *, project_id: str | None) -> list[str]:
    """问句命中的稳定概念页 identifier（``wiki:{path}``），至多 ``WIKI_BLEND_K`` 条。

    先取管线自身的 FTS-first 融合命中，再按稳定 kind 过滤；融合命中为
    空时不查页表。项目隔离下 FTS 成分被融合函数自行跳过（FTS 表无项目维
    度），keyword + embedded 成分仍按项目过滤，与 blend 行为一致。
    """
    try:
        from .wiki.retrieve import _wiki_blend_hits

        hits = _wiki_blend_hits(question, k=WIKI_BLEND_K, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - 路由信号缺失即无命中
        logger.debug("query router: wiki blend signal failed: %s", exc)
        return []
    if not hits:
        return []
    stable = _stable_wiki_paths()
    return [
        ev.identifier
        for ev in hits
        if (ev.identifier or "").removeprefix("wiki:") in stable
    ]


def route_query(
    question: str,
    *,
    project_id: str | None = None,
    explicit_deep: bool = False,
) -> RouteDecision:
    """确定性路由：三个已有信号按优先级合成一路。永不抛错。

    优先级：显式 deep > 结构化信号 > 萃取/深度类 > 稳定概念命中 > 其余。
    """
    q = (question or "").strip()
    if explicit_deep:
        return RouteDecision(
            route="deep", reason="explicit deep=true", explicit_deep=True
        )
    if not q:
        return RouteDecision(route="hybrid", reason="empty query")
    try:
        from .text2sql import classify_intent

        intent = classify_intent(q)
    except Exception as exc:  # noqa: BLE001 - 信号缺失即视为 unstructured
        logger.debug("query router: classify_intent failed: %s", exc)
        intent = {"route": "unstructured", "reason": "intent signal unavailable"}
    intent_route = str(intent.get("route") or "unstructured")
    intent_reason = str(intent.get("reason") or "")
    try:
        from .pageindex.two_stage import classify_pageindex_query

        pi_allowed, pi_reason = classify_pageindex_query(q)
    except Exception as exc:  # noqa: BLE001 - 信号缺失即视为不放行
        logger.debug("query router: classify_pageindex_query failed: %s", exc)
        pi_allowed, pi_reason = False, "pageindex signal unavailable"
    # 三信号先全部算出再按优先级合成：决策里须记录全部信号（供日志 / 抽查
    # 复核），不能因某一路优先返回就丢掉其余信号的实测值。
    stable_pages = _wiki_stable_pages(q, project_id=project_id)
    if intent_route in ("structured", "hybrid"):
        return RouteDecision(
            route="structured",
            reason=f"text2sql intent={intent_route}: {intent_reason}",
            intent_route=intent_route,
            intent_reason=intent_reason,
            pageindex_allowed=bool(pi_allowed),
            pageindex_reason=str(pi_reason or ""),
            wiki_stable_hit=bool(stable_pages),
            wiki_stable_pages=stable_pages,
        )
    if pi_allowed:
        return RouteDecision(
            route="deep",
            reason=f"pageindex: {pi_reason}",
            intent_route=intent_route,
            intent_reason=intent_reason,
            pageindex_allowed=True,
            pageindex_reason=str(pi_reason or ""),
            wiki_stable_hit=bool(stable_pages),
            wiki_stable_pages=stable_pages,
        )
    if stable_pages:
        return RouteDecision(
            route="wiki_first",
            reason=f"stable wiki hit: {', '.join(stable_pages)}",
            intent_route=intent_route,
            intent_reason=intent_reason,
            pageindex_allowed=bool(pi_allowed),
            pageindex_reason=str(pi_reason or ""),
            wiki_stable_hit=True,
            wiki_stable_pages=stable_pages,
        )
    return RouteDecision(
        route="hybrid",
        reason="no structured / pageindex / stable-wiki signal",
        intent_route=intent_route,
        intent_reason=intent_reason,
        pageindex_allowed=bool(pi_allowed),
        pageindex_reason=str(pi_reason or ""),
        wiki_stable_hit=bool(stable_pages),
        wiki_stable_pages=stable_pages,
    )
