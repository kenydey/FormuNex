"""Read-only regulatory agent (方案 §5.6).

Four tools, all strictly read-only: they query the substance database, run the
GB 30000 engine in-memory, or retrieve KB evidence. Nothing here inserts,
updates or deletes any row — in particular no locked classification, no
document and no release state is ever touched. The agent never persists a
classification result (``service.persist`` is deliberately not called).

Intents: ``substance`` (look up one substance's classifications + sources),
``explain`` (explain a composition's classification from its trace),
``substitute`` (lower-hazard replacements for the worst contributors),
``standard`` (GB / national-standard Q&A from KB evidence restricted to
regulatory sources), ``auto`` (keyword routing).
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.models_regulatory import SubstanceClassificationRow, SubstanceRow
from ..services.plm.ghs.model import severity
from ..services.plm.regulatory import cas as cas_mod
from ..services.plm.regulatory.source_priority import effective

_CAS_RE = re.compile(r"\d{2,7}-\d{2}-\d")
# KB evidence counts as a regulatory source when its title / snippet mentions one of these.
_REGULATORY_PATTERNS = ("GB", "GB/T", "GBZ", "国标", "法规", "标准", "化学品安全", "GHS", "危险化学品",
                        "安全技术说明书", "SDS", "分类", "标签")
_SUBSTANCE_STOPWORDS = ("查一下", "查询", "查找", "请问", "的分类", "分类是什么", "有哪些危害", "危害",
                        "物质", "化学品", "是什么", "？", "?", "，", ",", "。", "、", "的", "一下")

# Contributors this severe (category 2 or worse) are substitution targets.
_SEVERE_THRESHOLD = severity("2")


def _classification_dicts(rows: list[SubstanceClassificationRow]) -> list[dict[str, Any]]:
    return [{"hazard_class": r.hazard_class, "category": r.category, "h_code": r.h_code, "source": r.source,
             "is_minimum": r.is_minimum, "locked": r.locked_at is not None} for r in effective(rows)]


def lookup_substance(session: Session, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Substance classifications + sources (same query logic as GET /api/plm/substances)."""
    q = (query or "").strip()
    if not q:
        return []
    norm = cas_mod.normalize(q)
    if norm:
        stmt = select(SubstanceRow).where(SubstanceRow.cas_no == norm).limit(limit)
    else:
        like = f"%{q}%"
        stmt = (select(SubstanceRow)
                .where(or_(SubstanceRow.name_zh.like(like), SubstanceRow.name_en.ilike(like)))
                .order_by(SubstanceRow.hazardous_catalog_no.is_(None), SubstanceRow.name_zh)
                .limit(limit))
    subs = session.execute(stmt).scalars().all()
    if not subs:
        return []
    ids = [s.id for s in subs]
    cls_rows = session.execute(
        select(SubstanceClassificationRow).where(SubstanceClassificationRow.substance_id.in_(ids))).scalars().all()
    by_sub: dict[str, list] = {}
    for r in cls_rows:
        by_sub.setdefault(r.substance_id, []).append(r)
    return [{"id": s.id, "cas_no": s.cas_no, "name_zh": s.name_zh, "name_en": s.name_en,
             "hazardous_catalog_no": s.hazardous_catalog_no, "highly_toxic": s.highly_toxic,
             "precursor_control": s.precursor_control, "explosive_precursor": s.explosive_precursor,
             "classifications": _classification_dicts(by_sub.get(s.id, []))} for s in subs]


def _worst_severity(hazards: list[dict[str, Any]]) -> int | None:
    sev = [severity(h.get("category") or "") for h in hazards]
    return min(sev) if sev else None


def explain_classification(session: Session, components: list[dict[str, Any]],
                           properties: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify a composition and explain each outcome from the engine trace (in-memory only)."""
    from ..services.plm.ghs import service

    result, _built = service.build_and_classify(session, components, properties or {}, cached=True)
    response = service.to_response(session, result, cached=True)
    explanations = [{
        "hazard_class": t.hazard_class, "outcome": t.outcome, "rule": t.rule, "standard_ref": t.standard_ref,
        "explanation": t.explanation,
        "contributors": [{"name": c.name, "cas": c.cas, "pct": c.pct, "category": c.category, "note": c.note}
                         for c in t.contributors],
        "numbers": t.numbers,
    } for t in result.trace]
    if result.classified:
        outcomes = "、".join(f"{h.hazard_class} {h.category}（{','.join(h.h_codes) or '无 H 编码'}）"
                             for h in result.hazards)
        summary = f"该配方按 GB 30000 分类为：{outcomes}。信号词：{result.signal_word or '无'}。"
    else:
        summary = "该配方按 GB 30000 未分类为危险化学品。"
    if result.data_gaps:
        summary += f" 数据缺口 {len(result.data_gaps)} 项：" + "；".join(g.message for g in result.data_gaps[:3])
    return {"classification": response, "explanations": explanations,
            "data_gaps": [{"code": g.code, "hazard_class": g.hazard_class, "message": g.message,
                           "severity": g.severity} for g in result.data_gaps],
            "summary": summary}


def suggest_substitutions(session: Session, components: list[dict[str, Any]],
                          properties: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
    """Lower-hazard replacements for the composition's worst contributors.

    Candidates come from the chemist-rules table only (``allow_llm=False``:
    deterministic, no external call). Each candidate is verified against the
    substance database: a candidate counts as ``lower`` only when its worst
    classification is strictly less severe than the target's (or it is
    assessed and not classified). Candidates absent from the database are
    reported as ``unknown`` — never claimed safer without evidence.
    """
    from ..services.llm_alternatives import fetch_llm_alternatives
    from ..services.plm.ghs import service

    result, built = service.build_and_classify(session, components, properties or {}, cached=True)
    response = service.to_response(session, result, cached=True)

    # Worst contributors: components with a category-2-or-worse hazard, ranked by severity then share.
    roles = {str(c.get("name") or ""): c.get("role") for c in components}
    targets = []
    for comp in built.components:
        if not comp.hazards:
            continue
        worst = min(severity(h.category) for h in comp.hazards)
        if worst <= _SEVERE_THRESHOLD:
            targets.append({"component": comp, "worst": worst})
    targets.sort(key=lambda t: (t["worst"], -t["component"].pct))
    targets = targets[: max(1, limit)]

    suggestions = []
    for t in targets:
        comp = t["component"]
        target_hazards = [{"hazard_class": h.hazard_class, "category": h.category, "h_code": h.h_code,
                           "source": h.source} for h in comp.hazards]
        cands = fetch_llm_alternatives(material=comp.name, role_hint=roles.get(comp.name),
                                       limit=3, known_names=[c.get("name") or "" for c in components],
                                       allow_llm=False).get("llm") or []
        alternatives = []
        for cand in cands:
            name = cand.get("name") or ""
            found = lookup_substance(session, name, limit=1)
            row = found[0] if found else None
            if row is None:
                verdict, note = "unknown", "该候选物不在物质库中，未验证其危害，替换前需法规专员评估。"
                cand_hazards: list[dict[str, Any]] = []
            else:
                cand_hazards = row["classifications"]
                cand_worst = _worst_severity(cand_hazards)
                if cand_worst is None:
                    verdict, note = "lower", "物质库中已评估且未分类，危害低于目标组分。"
                elif cand_worst > t["worst"]:
                    verdict, note = "lower", "最严重分类类别低于目标组分。"
                else:
                    verdict, note = "comparable", "最严重分类类别不低于目标组分，不建议替换。"
            alternatives.append({"name": name, "cas_no": row["cas_no"] if row else None,
                                 "verdict": verdict, "note": note,
                                 "classifications": cand_hazards,
                                 "rationale": cand.get("rationale") or "",
                                 "source": cand.get("source") or "chemist_rules"})
        suggestions.append({
            "target": {"name": comp.name, "cas": comp.cas, "pct": comp.pct, "hazards": target_hazards},
            "alternatives": alternatives,
        })
    return {"classification": response, "suggestions": suggestions,
            "data_gaps": [{"code": g.code, "message": g.message} for g in result.data_gaps],
            "summary": ("未找到类别 2 及以上的危害组分，无需替换。"
                        if not suggestions else
                        f"找到 {len(suggestions)} 个高危害组分（类别 2 及以上），共 {sum(len(s['alternatives']) for s in suggestions)} 个候选替代物。")}


def answer_standard_question(session: Session, question: str, k: int = 5) -> dict[str, Any]:
    """GB / national-standard Q&A: KB retrieval restricted to regulatory sources, extractive only.

    No LLM synthesis: the answer quotes the retrieved snippets with their
    source titles. When no regulatory source matches, it says so instead of
    answering from general documents.
    """
    try:
        from ..services.kb_index import retrieve_evidence

        evidence = retrieve_evidence(question, k=k, include_global=True)
    except Exception:  # noqa: BLE001 - KB unavailable: fail open with an explicit "not found" answer
        evidence = []
    reg = []
    for ev in evidence or []:
        hay = f"{ev.title or ''} {ev.snippet or ''} {ev.source or ''}"
        if any(p in hay for p in _REGULATORY_PATTERNS):
            reg.append({"title": ev.title, "snippet": ev.snippet, "source": ev.source,
                        "relevance": ev.relevance, "page": ev.page})
    if not reg:
        return {"evidence": [],
                "summary": "知识库中未找到与该问题相关的法规类文档（GB / 国标 / 法规 / 标准）。请提供法规文档编号（如 GB 30000.7）或联系法规专员。"}
    lines = [f"[{i + 1}] {e['title']}（{e['source'] or ''}）: {e['snippet']}" for i, e in enumerate(reg[:3])]
    return {"evidence": reg,
            "summary": "根据知识库中的法规类文档，相关内容如下：\n" + "\n".join(lines)}


def _extract_substance_query(question: str) -> str:
    m = _CAS_RE.search(question or "")
    if m:
        return m.group(0)
    q = question or ""
    for w in _SUBSTANCE_STOPWORDS:
        q = q.replace(w, "")
    return q.strip() or question


def ask(session: Session, *, question: str, intent: str = "auto",
        components: list[dict[str, Any]] | None = None,
        properties: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
    """Route one regulatory question to the right read-only tool(s)."""
    q = (question or "").strip()
    if intent == "auto":
        if components and any(k in q for k in ("替代", "替换", "降低", "降危害", "更安全", "换成")):
            intent = "substitute"
        elif components and any(k in q for k in ("为什么", "解释", "依据", "分类", "怎么算", "如何判定", "判定")):
            intent = "explain"
        elif _CAS_RE.search(q) or any(k in q for k in ("物质", "CAS", "查一下", "查询")):
            intent = "substance"
        elif any(k in q for k in ("GB", "国标", "标准", "条款", "法规", "要求", "规定", "GHS", "标签")):
            intent = "standard"
        elif components:
            intent = "explain"
        else:
            intent = "standard"

    if intent == "substance":
        rows = lookup_substance(session, _extract_substance_query(q), limit=limit)
        if not rows:
            answer = f"物质库中未找到与“{q}”匹配的物质。请用 CAS 号（如 1330-20-7）重试，或确认物质名。"
        else:
            parts = []
            for r in rows:
                cls = r["classifications"]
                desc = ("未分类" if not cls else "、".join(
                    f"{c['hazard_class']} {c['category']}（{c['h_code'] or ''}，来源：{c['source']}）" for c in cls))
                parts.append(f"{r['name_zh'] or r['name_en']}（CAS {r['cas_no'] or '无'}）：{desc}")
            answer = "；".join(parts)
        return {"intent": intent, "question": question, "answer": answer, "tools_used": ["lookup_substance"],
                "evidence": rows}

    if intent == "explain":
        if not components:
            return {"intent": intent, "question": question, "answer": "解释分类需要提供配方组分（components）。",
                    "tools_used": ["explain_classification"], "evidence": []}
        out = explain_classification(session, components, properties)
        return {"intent": intent, "question": question, "answer": out["summary"],
                "tools_used": ["explain_classification"], "evidence": out["explanations"],
                "classification": out["classification"], "data_gaps": out["data_gaps"]}

    if intent == "substitute":
        if not components:
            return {"intent": intent, "question": question, "answer": "给出降危害建议需要提供配方组分（components）。",
                    "tools_used": ["suggest_substitutions"], "evidence": []}
        out = suggest_substitutions(session, components, properties, limit=limit)
        return {"intent": intent, "question": question, "answer": out["summary"],
                "tools_used": ["suggest_substitutions"], "evidence": out["suggestions"],
                "classification": out["classification"], "suggestions": out["suggestions"],
                "data_gaps": out["data_gaps"]}

    # standard
    out = answer_standard_question(session, q, k=limit)
    return {"intent": intent, "question": question, "answer": out["summary"],
            "tools_used": ["answer_standard_question"], "evidence": out["evidence"]}
