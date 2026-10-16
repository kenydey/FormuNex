"""Concentration bands for SDS section 3 (GB/T 17519 trade-secret disclosure).

An exact recipe share is a trade secret; section 3 may state a range instead
(14.5 % → "10%～20%"). Band edges are configurable per company; the default
keeps the low end fine-grained where hazard thresholds sit (0.1 %, 1 %, 5 %).
"""
from __future__ import annotations

DEFAULT_EDGES: tuple[float, ...] = (0.1, 1, 3, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)


def _fmt(x: float) -> str:
    return f"{x:g}%"


def band(pct: float, edges: tuple[float, ...] = DEFAULT_EDGES) -> str:
    """Lower edge inclusive, upper edge exclusive (except 100 %)."""
    if pct < edges[0]:
        return f"<{_fmt(edges[0])}"
    lo = edges[0]
    for hi in edges[1:]:
        if pct < hi or (hi == edges[-1] and pct <= hi):
            return f"{_fmt(lo)}～{_fmt(hi)}"
        lo = hi
    return f"≥{_fmt(edges[-1])}"


def disclose(pct: float, *, trade_secret: bool, edges: tuple[float, ...] = DEFAULT_EDGES) -> str:
    return band(pct, edges) if trade_secret else f"{round(pct, 2):g}%"
