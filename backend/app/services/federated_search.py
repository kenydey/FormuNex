"""Federated multi-source search facade for CRAG fallback ingestion."""
from __future__ import annotations

import time
from typing import Callable

from loguru import logger
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence, Requirement
from ..services.deep_research.models import RetrievalHit
from . import literature


class FederatedSearchResult(BaseModel):
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    query: str = ""
    elapsed_ms: float = 0.0


class FederatedSearchEngine:
    """Wrap ``literature.iter_search`` with typed results and logging."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def effective_sources(self) -> list[str]:
        sources = list(self._settings.federated_sources)
        return sources

    def search(
        self,
        query: str,
        *,
        source_types: list[str] | None = None,
        req: Requirement | None = None,
        total_limit: int | None = None,
        per_source_cap: int | None = None,
        progress_cb: Callable[[list[Evidence]], None] | None = None,
        # W4-6 · P0-18: 日期/域名过滤（可选；None/空 = 不过滤，向后兼容）
        date_from: str | int | None = None,
        date_to: str | int | None = None,
        domain_allowlist: list[str] | None = None,
    ) -> FederatedSearchResult:
        """Federated search with post-filters and refill.

        P2-7 (2026-11-26 review): 日期/域名过滤在截断后执行、不回填 ——
        过滤后返回数可远少于 ``total_limit``，即使符合条件的行在被截掉的
        尾部还存在。过滤无法下推到 ``iter_search``（它没有日期/域名维度的
        服务端参数），故改为补跑回填：过滤后不足时用加倍的 limit 再跑一次
        （最多 3 次，上限 ``settings.search_total_limit``），直到凑满或到顶。
        默认 limit 与 ``iter_search`` 统一为 ``settings.search_total_limit``
       （旧默认 ``min(120, search_total_limit)`` 与之不一致）。
        """
        types = source_types or self.effective_sources()
        limit = total_limit or self._settings.search_total_limit
        cap = per_source_cap or self._settings.search_limit_per_source
        t0 = time.perf_counter()

        logger.info("FederatedSearch query={!r} sources={}", query[:80], types)
        filtering = (
            date_from is not None or date_to is not None or bool(domain_allowlist)
        )
        evidence: list[Evidence] = []
        fetch_limit = limit
        for _attempt in range(3):
            try:
                evidence = literature.iter_search(
                    query,
                    types,
                    req=req,
                    total_limit=fetch_limit,
                    per_source_cap=cap,
                    progress_cb=progress_cb,
                )[0]
            except Exception as exc:
                logger.exception("FederatedSearch failed: {}", exc)
                evidence = []
                break  # 检索本身失败不补跑：补跑只针对"过滤后不足"，不针对取数失败

            # W4-6 · P0-18: 后过滤（年份未知项保留；白名单为空不过滤）
            if date_from is not None or date_to is not None:
                from .citation_date_guard import filter_evidence_by_date

                evidence = filter_evidence_by_date(
                    evidence, date_from=date_from, date_to=date_to
                )
            if domain_allowlist:
                from .citation_date_guard import filter_evidence_by_domain

                evidence = filter_evidence_by_domain(evidence, domain_allowlist)

            # P2-7: 过滤后不足且未到上限时，用加倍的 limit 补跑回填。
            if (
                not filtering
                or len(evidence) >= limit
                or fetch_limit >= self._settings.search_total_limit
            ):
                break
            fetch_limit = min(fetch_limit * 2, self._settings.search_total_limit)
            logger.info(
                "FederatedSearch refill: %d/%d after filters, retrying with total_limit=%d",
                len(evidence),
                limit,
                fetch_limit,
            )
        evidence = evidence[:limit]

        hits = [RetrievalHit.from_evidence(ev) for ev in evidence]
        counts: dict[str, int] = {}
        for h in hits:
            counts[h.source] = counts.get(h.source, 0) + 1

        elapsed = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "FederatedSearch done hits={} sources={} elapsed_ms={:.0f}",
            len(evidence),
            counts,
            elapsed,
        )
        return FederatedSearchResult(
            hits=hits,
            evidence=evidence,
            source_counts=counts,
            query=query,
            elapsed_ms=elapsed,
        )
