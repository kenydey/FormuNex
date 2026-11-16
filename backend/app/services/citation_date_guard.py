"""W4-6 · P0-18: 搜索日期/域名过滤 + LLM 输出日期绝对校验。

防幻觉：LLM 输出中引用了"未来日期"论文（如 2028 年发表的论文，
而今天是 2026 年）几乎一定是编造的。本模块提供：

* ``filter_evidence_by_date`` / ``filter_evidence_by_domain`` —— 检索后过滤
  （被 ``FederatedSearchEngine.search`` 的可选参数调用，默认关闭）。
* ``validate_llm_citation_dates`` —— 纯函数，扫描 Markdown 脚注定义中的年份，
  标出未来年份的引用（供 preflight / reviewer 调用，不抛错）。
* ``future_pub_date_penalty`` —— 检索排序融合中的未来日期降权项。
"""
from __future__ import annotations

import re
from ..clock import utcnow
from typing import Any
from urllib.parse import urlparse

# 匹配 1500–2199 的四位年份（含 "2027年" 这类中文写法）。
# 注意：该正则也用于结构化元数据（parse_pub_year，显式年份字段，无歧义）。
# 自由文本脚注扫描（validate_llm_citation_dates）不得直接用它：来源标识、
# 标准号、页码里的裸四位数不是年份（2026-11-16 CI 误报：脚注来源标识
# "2112 6ce-..." 被当成未来年份拦住 storm 导出），须经 _has_date_context
# 确认处于日期语境才算。
_YEAR_RE = re.compile(r"(1[5-9]\d{2}|20\d{2}|21\d{2})")
# 日期语境词：出现在年份之前（允许尾随空格），如"发表于 2028"、"March 2028"、
# "Author et al. 2028"（作者-年份引用格式）。
_DATE_CUE_BEFORE_RE = re.compile(
    r"(发表于|发表|出版于|出版|published|publ\.?|year|年份|et al\.?"
    r"|January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)\s*$",
    re.IGNORECASE,
)
# 年份之后跟英文月份名，如 "2028 March"。
_MONTH_AFTER_RE = re.compile(
    r"\s+(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)",
    re.IGNORECASE,
)
# 脚注定义行：[^n]: ...
_FOOTNOTE_DEF_RE = re.compile(r"\[\^(\d+)\]:")

#: 未来日期引用在排序中的降权幅度（与 authority bonus 同量级）。
FUTURE_DATE_PENALTY = -0.15


def parse_pub_year(value: Any) -> int | None:
    """Best-effort 年份提取：int / "2023" / "2023-05-01" / "2023年" → 2023。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1500 <= value <= 2199 else None
    m = _YEAR_RE.search(str(value))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def evidence_year(ev: Any) -> int | None:
    """Resolve an evidence's publication year: ``pub_year`` 优先，回退解析 ``pub_date``。"""
    year = parse_pub_year(getattr(ev, "pub_year", None))
    if year is not None:
        return year
    return parse_pub_year(getattr(ev, "pub_date", None))


def filter_evidence_by_date(
    evidence: list[Any],
    *,
    date_from: str | int | None = None,
    date_to: str | int | None = None,
) -> list[Any]:
    """按出版年份过滤（闭区间）。年份未知 → 保留（fail-open，不过滤掉无元数据项）。

    ``date_from`` / ``date_to`` 接受年份 int 或 "YYYY" / "YYYY-MM-DD" 字符串。
    """
    y_from = parse_pub_year(date_from)
    y_to = parse_pub_year(date_to)
    if y_from is None and y_to is None:
        return list(evidence)
    out: list[Any] = []
    for ev in evidence:
        y = evidence_year(ev)
        if y is None:
            out.append(ev)  # 未知年份：保留
            continue
        if y_from is not None and y < y_from:
            continue
        if y_to is not None and y > y_to:
            continue
        out.append(ev)
    return out


def _evidence_hosts(ev: Any) -> set[str]:
    hosts: set[str] = set()
    for attr in ("url", "url_alt"):
        raw = getattr(ev, attr, None)
        if not raw:
            continue
        try:
            host = urlparse(str(raw)).hostname or ""
        except Exception:
            host = ""
        if host:
            hosts.add(host.lower())
    return hosts


def _host_allowed(host: str, wanted: str) -> bool:
    """域名边界匹配：精确相等，或为其子域名（``sub.example.com`` 匹配白名单
    ``example.com``）。禁止子串匹配 —— 否则白名单 ``example.com`` 会误命中
    ``evil-example.com``（B-14）。
    """
    return host == wanted or host.endswith("." + wanted)


def filter_evidence_by_domain(
    evidence: list[Any],
    allowlist: list[str] | None,
) -> list[Any]:
    """按域名白名单过滤。``allowlist`` 为空/None → 不过滤。

    匹配 ``url`` / ``url_alt`` 的 host（精确或子域名边界匹配，大小写不敏感）；
    URL 缺失时回退匹配 ``source`` 名称子串（如 "openalex"）。
    """
    wanted = [str(d).strip().lower() for d in (allowlist or []) if str(d).strip()]
    if not wanted:
        return list(evidence)
    out: list[Any] = []
    for ev in evidence:
        hosts = _evidence_hosts(ev)
        source = str(getattr(ev, "source", "") or "").lower()
        host_hit = any(_host_allowed(h, w) for h in hosts for w in wanted)
        source_hit = any(w in source for w in wanted)
        if host_hit or source_hit:
            out.append(ev)
    return out


def _has_date_context(rest: str, m: re.Match[str]) -> bool:
    """该四位数是否处于日期语境（是年份的证据），而非标识/编号。

    认作日期语境的情形（任一成立即可）：
    * 后跟"年"（如 2028年、2028 年）；
    * ISO 式日期的一部分（如 2028-03-15、2028/3/5）；
    * 括号内的年份（如 (2028)、（2028））；
    * 前有发表/出版类词、月份名或作者-年份引用（如"发表于 2028"、
      "March 2028"、"Author et al. 2028"）；
    * 后跟月份名（如 "2028 March"）。
    裸四位数（如来源标识 "2112 6ce-..."、标准号 "GB 2112"、页码
    "pp. 2112"）一律不算——它不是发表年份的证据。
    """
    after = rest[m.end() :]
    before = rest[: m.start()]
    if after.lstrip().startswith("年"):
        return True
    if re.match(r"\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}", after):
        return True
    if after.startswith(")") and before.rstrip().endswith("("):
        return True
    if after.startswith("）") and before.rstrip().endswith("（"):
        return True
    if _DATE_CUE_BEFORE_RE.search(before):
        return True
    if _MONTH_AFTER_RE.match(after):
        return True
    return False


def validate_llm_citation_dates(markdown: str) -> list[dict[str, Any]]:
    """绝对校验：扫描脚注定义中处于日期语境的年份，标出"未来年份"的引用。

    只认带日期语境的年份（后跟"年"、ISO 日期、括号内年份、发表/出版类
    词或月份名前后）；来源标识、标准号、页码里的裸四位数不算年份
    （2026-11-16 CI 误报：脚注来源标识 "2112 6ce-..." 被旧的裸正则
    当成未来年份，拦住了 storm 导出）。

    返回 findings 列表：``{"footnote": n, "year": y, "reason": "future_dated_citation",
    "excerpt": ...}``。纯函数，不抛错；无问题返回 []。
    """
    findings: list[dict[str, Any]] = []
    text = markdown or ""
    try:
        current_year = utcnow().year  # P3-A6: UTC year, not server-local
        for m in _FOOTNOTE_DEF_RE.finditer(text):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            line_end = text.find("\n", m.end())
            rest = text[m.end() : line_end if line_end >= 0 else None]
            for ym in _YEAR_RE.finditer(rest):
                if not _has_date_context(rest, ym):
                    continue
                y = int(ym.group(1))
                if y > current_year:
                    findings.append(
                        {
                            "footnote": n,
                            "year": y,
                            "reason": "future_dated_citation",
                            "excerpt": rest.strip()[:160],
                        }
                    )
                    break  # 每条脚注只报一次
    except Exception:
        return findings
    return findings


def future_pub_date_penalty(ev: Any) -> float:
    """出版年份在未来 → 降权（防元数据错误/幻觉污染排序）；否则 0.0。"""
    try:
        y = evidence_year(ev)
    except Exception:
        return 0.0
    if y is not None and y > utcnow().year:  # P3-A6: UTC year
        return FUTURE_DATE_PENALTY
    return 0.0
