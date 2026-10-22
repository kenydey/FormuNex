"""Release gate (方案 §5.5.10): the ten automatic checks a product revision must pass before sign-off.

Each check returns a status and the individual failing *items*; an item of a
waivable check (objective targets, non-blocking data gaps) can be cleared by an
e-signed waiver. Red lines, classification currency and document checks are
never waivable — they are fixed by changing the data and regenerating.

The gate reads documents through their structured ``content`` (``meta.facts``
holds the language-neutral values), never by parsing rendered text.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....config import get_settings
from ....db.models import FormulationVersion, MeasurementRow
from ....db.models_plm import (
    BatchRow,
    ClassificationResultRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
    ReleaseGateWaiverRow,
)
from ....db.models_regulatory import SubstanceClassificationRow
from ...compliance_rules import rohs_restricted
from ..ghs import service as ghs_service
from ..ghs.inputs import build_components
from ..ghs.ruleset import load_ruleset, load_signoff
from ..regulatory.source_priority import rank

TOTAL_TOLERANCE_PCT = 0.01
# Minimum classification source (方案: "分类来源 ≥ 指定置信度"): cn_catalog or better.
MIN_SOURCE = "cn_catalog"
# A component is "key" — its classification must be locked by an EHS specialist — at or above
# KEY_PCT, or when it carries a hazard and is present at HAZARDOUS_KEY_PCT or more.
KEY_PCT = 10.0
HAZARDOUS_KEY_PCT = 1.0
# Cross-document tolerances (absolute) for values that appear in several documents.
CONSISTENCY_TOLERANCE: dict[str, float] = {
    "density_g_cm3": 0.02, "ph": 0.5, "solids_content_pct": 2.0, "flash_point_c": 2.0, "voc_g_l": 5.0,
}
WAIVABLE = {"objectives": "spec.write", "data_gaps": "regulatory.review"}

CHECKS: tuple[tuple[str, str, str], ...] = (
    ("formula_total", "G1", "配方合计 = 100%"),
    ("substance_data", "G2", "物质分类来源可信且关键组分已锁定"),
    ("objectives", "G3", "目标契约关键指标达标（已复核实测）"),
    ("red_lines", "G4", "合规红线零命中"),
    ("classification_current", "G5", "分类结果基于当前规则集与法规数据"),
    ("documents", "G6", "必备文档齐全且已审核"),
    ("consistency", "G7", "跨文档数值一致"),
    ("sds_label", "G8", "SDS 第 2 部分与标签要素一致"),
    ("required_fields", "G9", "必填字段完整"),
    ("data_gaps", "G10", "数据缺口已逐条处置"),
)


class GateError(ValueError):
    pass


@dataclass
class Check:
    key: str
    code: str
    title: str
    items: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    not_applicable: bool = False

    def fail(self, item_key: str, message: str, *, waivable: bool | None = None) -> None:
        self.items.append({"key": item_key, "message": message,
                           "waivable": self.key in WAIVABLE if waivable is None else waivable})

    def to_dict(self, waivers: dict[tuple[str, str, str | None], ReleaseGateWaiverRow]) -> dict[str, Any]:
        items = []
        for it in self.items:
            # A waiver only applies when it was granted for this exact failure
            # text: a changed limit or measured value changes the message, and
            # the old waiver must not silently carry over (P0-2).
            w = waivers.get((self.key, it["key"], waiver_content_hash(it["message"]))) if it["waivable"] else None
            items.append({**it, "waived": w is not None,
                          "waiver": {"id": w.id, "reason": w.reason, "waived_by": w.waived_by,
                                     "at": w.created_at.isoformat()} if w else None})
        open_items = [i for i in items if not i["waived"]]
        status = "pass" if not items else ("waived" if not open_items else "fail")
        return {"key": self.key, "code": self.code, "title": self.title, "status": status,
                "waivable": self.key in WAIVABLE, "items": items, "notes": self.notes, "evidence": self.evidence}


def waiver_content_hash(message: str) -> str:
    """Bind a waiver to the exact failure text it waives (sha256 of the message).

    The gate item message carries the values that matter (limits, measured
    values, missing lists); hashing it means any change to those values
    invalidates the waiver, instead of the old (check_code, item_key) key
    silently re-applying it.
    """
    return hashlib.sha256((message or "").encode()).hexdigest()


def item_key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return raw if len(raw) <= 120 else raw[:100] + "#" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def required_docs() -> list[tuple[str, str]]:
    out = []
    for part in get_settings().plm_release_required_docs.split(","):
        part = part.strip()
        if not part:
            continue
        doc_type, _, lang = part.partition(":")
        out.append((doc_type.strip(), (lang or "*").strip()))
    return out


# --------------------------------------------------------------------------- document selection


def latest_documents(session: Session, rev: ProductRevisionRow) -> dict[tuple[str, str], RegulatoryDocumentRow]:
    """Latest version of each (type, lang) for the revision; COA keyed per batch as ("coa", "<lang>@<batch>")."""
    rows = session.execute(
        select(RegulatoryDocumentRow).where(RegulatoryDocumentRow.revision_id == rev.id)
        .order_by(RegulatoryDocumentRow.version_no)
    ).scalars().all()
    out: dict[tuple[str, str], RegulatoryDocumentRow] = {}
    for r in rows:
        key = (r.doc_type, f"{r.lang}@{r.batch_id}" if r.doc_type == "coa" else r.lang)
        out[key] = r
    return out


def select_package_documents(session: Session, rev: ProductRevisionRow
                             ) -> tuple[list[RegulatoryDocumentRow], list[tuple[str, str, str]]]:
    """The documents a release would contain, and what is missing: ``(doc_type, lang, reason)``."""
    latest = latest_documents(session, rev)
    chosen: list[RegulatoryDocumentRow] = []
    problems: list[tuple[str, str, str]] = []
    for doc_type, lang in required_docs():
        if doc_type == "coa":
            coas = [d for (t, _k), d in latest.items() if t == "coa" and (lang == "*" or d.lang == lang)]
            approved = [d for d in coas if d.status == "approved" and (d.content.get("meta") or {}).get("overall") == "PASS"]
            if approved:
                chosen.append(min(approved, key=lambda d: d.created_at))  # the first released batch
            elif coas:
                problems.append(("coa", lang, "COA 尚未审核签发（或结论不合格）"))
            else:
                problems.append(("coa", lang, "缺少首批 COA"))
            continue
        langs = [lang] if lang != "*" else sorted({k for (t, k) in latest if t == doc_type})
        if not langs:
            problems.append((doc_type, lang, "缺少文档"))
        for lg in langs:
            d = latest.get((doc_type, lg))
            if d is None:
                problems.append((doc_type, lg, "缺少文档"))
            elif d.status != "approved":
                problems.append((doc_type, lg, f"最新版本 v{d.version_no} 未审核（状态 {d.status}）"))
                chosen.append(d)
            else:
                chosen.append(d)
    return chosen, problems


# --------------------------------------------------------------------------- the checks


def _items(session: Session, rev: ProductRevisionRow) -> tuple[FormulationVersion | None, list[dict]]:
    if not rev.formulation_version_id:
        return None, []
    fv = session.get(FormulationVersion, rev.formulation_version_id)
    if fv is None:
        return None, []
    return fv, ghs_service.formulation_version_items(session, fv.id)


def _check_total(c: Check, fv: FormulationVersion | None) -> None:
    if fv is None:
        c.fail("formulation_version", "修订版未关联配方版本")
        return
    total = sum(float(i.get("weight_pct") or 0) for i in (fv.snapshot or {}).get("ingredients") or [])
    c.evidence["total_pct"] = round(total, 6)
    if abs(total - 100.0) > TOTAL_TOLERANCE_PCT:
        c.fail("total", f"配方合计 {total:.4f}%，与 100% 相差超过 {TOTAL_TOLERANCE_PCT}%")


def _check_substances(session: Session, c: Check, items: list[dict]) -> list:
    if not items:
        c.fail("formulation_version", "没有可评估的配方组分")
        return []
    built = build_components(session, items, load_ruleset())
    sub_ids = [x.substance_id for x in built.components if x.substance_id]
    locked = set(session.execute(
        select(SubstanceClassificationRow.substance_id).where(
            SubstanceClassificationRow.substance_id.in_(sub_ids), SubstanceClassificationRow.source == "manual",
            SubstanceClassificationRow.locked_at.is_not(None))
    ).scalars()) if sub_ids else set()
    for comp in built.components:
        label = f"{comp.name}（{comp.cas or '无 CAS'}，{comp.pct:g}%）"
        k = item_key(comp.substance_id or comp.cas or comp.name)
        if not comp.assessed:
            c.fail(k, f"{label}：危害未评估")
            continue
        weak = sorted({h.source for h in comp.hazards if rank(h.source) > rank(MIN_SOURCE)})
        if weak:
            c.fail(k, f"{label}：分类来源置信度不足（{', '.join(weak)}），需供应商 SDS 或法规专员确认")
        is_key = comp.pct >= KEY_PCT or (comp.hazards and comp.pct >= HAZARDOUS_KEY_PCT)
        if is_key and comp.substance_id and comp.substance_id not in locked:
            c.fail(k + ":lock", f"{label}：关键组分的分类尚未由法规专员锁定")
    c.evidence["components"] = len(built.components)
    return built.components


def _check_objectives(session: Session, c: Check, rev: ProductRevisionRow) -> None:
    acceptance = (rev.spec_snapshot or {}).get("acceptance") or []
    if not acceptance:
        c.notes.append("规格快照未定义关键指标（spec_snapshot.acceptance），本项不适用")
        c.not_applicable = True
        return
    measured: dict[str, MeasurementRow] = {}
    if rev.winning_experiment_id is not None:
        for m in session.execute(
            select(MeasurementRow).where(MeasurementRow.experiment_id == rev.winning_experiment_id,
                                         MeasurementRow.review_status == "reviewed")
            .order_by(MeasurementRow.created_at)
        ).scalars():
            measured[m.metric] = m
    else:
        c.notes.append("修订版未指定定型实验（winning_experiment_id）")
    for a in acceptance:
        metric = a.get("metric")
        label = a.get("label") or metric
        m = measured.get(metric)
        if m is None:
            c.fail(item_key(metric), f"{label}：无已复核的实测值")
            continue
        lo, hi = a.get("min"), a.get("max")
        if (lo is not None and m.value < lo) or (hi is not None and m.value > hi):
            rng = f"{'' if lo is None else f'≥{lo:g}'}{' ' if lo is not None and hi is not None else ''}" \
                  f"{'' if hi is None else f'≤{hi:g}'}"
            c.fail(item_key(metric), f"{label}：实测 {m.value:g}{m.unit or ''} 未达到 {rng}")
        c.evidence[metric] = {"value": m.value, "measurement_id": m.id}


def _ghs_constraints_for_revision(session: Session, rev: ProductRevisionRow):
    """GHS constraints for a revision (Phase 1D, 1-14).

    The revision snapshot is the frozen contract and wins per-field, but the
    GHS fields (``forbidden_pictograms`` / ``forbidden_signal_word`` /
    ``voc_limit_g_l``) were dropped whenever a spec was copied — so the owning
    project's spec contract fills any missing field. Substance ``red_lines``
    are NOT merged: the gate checks the snapshot's own (frozen) red lines.
    """
    from ...project_redlines import (
        GhsConstraints,
        constraints_from_spec_contract,
        load_project_constraints,
    )

    snap = constraints_from_spec_contract(rev.spec_snapshot or {}, source="spec_snapshot")
    if snap.forbidden_pictograms and snap.forbidden_signal_word and snap.voc_limit_g_l is not None:
        snap.substances = []
        return snap
    proj = GhsConstraints()
    product = session.get(ProductRow, rev.product_id) if rev.product_id else None
    if product is not None and product.project_id:
        proj = load_project_constraints(session, product.project_id)
    merged = GhsConstraints(
        substances=[],
        forbidden_pictograms=snap.forbidden_pictograms or proj.forbidden_pictograms,
        forbidden_signal_word=snap.forbidden_signal_word or proj.forbidden_signal_word,
        voc_limit_g_l=(snap.voc_limit_g_l if snap.voc_limit_g_l is not None else proj.voc_limit_g_l),
        source="spec_snapshot+project",
    )
    return merged


def _check_red_lines(session: Session, c: Check, rev: ProductRevisionRow, fv: FormulationVersion | None,
                     components: list, items: list) -> None:
    ingredients = (fv.snapshot or {}).get("ingredients") or [] if fv else []
    for ing in ingredients:
        hit = rohs_restricted(ing.get("name") or "", ing.get("cas_no"))
        if hit:
            c.fail(item_key("rohs", ing.get("cas_no") or ing.get("name")), f"{ing.get('name')}：{hit}", waivable=False)
    red_lines = (rev.spec_snapshot or {}).get("red_lines") or []
    pct_by_cas: dict[str, float] = {}
    for comp in components:
        if comp.cas:
            pct_by_cas[comp.cas] = pct_by_cas.get(comp.cas, 0.0) + comp.pct
    for ing in ingredients:
        if ing.get("cas_no") and ing["cas_no"] not in pct_by_cas:
            pct_by_cas[ing["cas_no"]] = float(ing.get("weight_pct") or 0)
    unresolved_refs: list[str] = []
    unmatched_class_refs = 0
    for rl in red_lines:
        # Phase 1D (1-16): list-referenced red lines ({"list_id", "list_entry_id"})
        # check the CLASS total over all the entry's CAS numbers.
        if rl.get("list_id") or rl.get("list_entry_id"):
            from ...compliance_lists import class_total_for_redline, unresolved_redline_reason

            resolved = class_total_for_redline(pct_by_cas, rl)
            if resolved is not None:
                found, label = resolved
                limit = float(rl.get("max_pct") or 0)
                if found > limit:
                    c.fail(item_key("red_line", label), f"{label}：合计含量 {found:g}% 超过红线 {limit:g}%"
                           + (f"（{rl['reason']}）" if rl.get("reason") else ""), waivable=False)
            elif (reason := unresolved_redline_reason(rl)) is not None:
                # P1-24 fail-closed: an unresolvable reference blocks the
                # gate instead of silently waiving the red line.
                ref = f"{rl.get('list_id') or ''}:{rl.get('list_entry_id') or ''}"
                unresolved_refs.append(ref)
                c.fail(item_key("red_line", "unresolved", ref), reason, waivable=False)
            else:
                # Known no-CAS class entry (reference-only by design): not a
                # failure, but counted in evidence instead of dropped silently.
                unmatched_class_refs += 1
            continue
        cas = rl.get("cas")
        limit = float(rl.get("max_pct") or 0)
        found = pct_by_cas.get(cas or "", 0.0)
        if cas and found > limit:
            c.fail(item_key("red_line", cas), f"{rl.get('name') or cas}：含量 {found:g}% 超过红线 {limit:g}%"
                   + (f"（{rl['reason']}）" if rl.get("reason") else ""), waivable=False)
    c.evidence["red_lines"] = len(red_lines)
    c.evidence["red_lines_unresolved"] = unresolved_refs
    c.evidence["red_lines_unmatched_class"] = unmatched_class_refs
    # Phase 1D (1-14): GHS constraints — forbidden pictograms / signal word /
    # VOC limit. Red lines are never waivable, so these fail the gate outright.
    ghs = _ghs_constraints_for_revision(session, rev)
    if ghs and (ghs.forbidden_pictograms or ghs.forbidden_signal_word or ghs.voc_limit_g_l is not None):
        snap_ings = [{"name": i.get("name") or "", "role": i.get("role") or "",
                      "weight_pct": i.get("weight_pct") or 0} for i in ingredients]
        try:
            from ...project_redlines import check_redlines

            result, _built = ghs_service.build_and_classify(session, items)
            # Fail-closed: a blocking data gap means the pictograms / signal
            # word cannot be determined — "cannot verify" is not "pass"
            # (red lines are never waivable). Without blocking gaps the
            # result's pictograms are authoritative, even when empty.
            blocking_gaps = [g for g in (result.data_gaps or [])
                             if getattr(g, "severity", None) == "blocking"]
            if blocking_gaps and (ghs.forbidden_pictograms or ghs.forbidden_signal_word):
                gap_msgs = "; ".join(g.message for g in blocking_gaps[:2])
                c.fail("ghs_classification",
                       f"无法确认象形图/信号词（分类被数据缺口阻塞：{gap_msgs}），"
                       f"无法复核禁止的象形图 {ghs.forbidden_pictograms} / 信号词 {ghs.forbidden_signal_word}",
                       waivable=False)
            hits = check_redlines(items, ghs, classification=result.to_dict(),
                                  voc_items=snap_ings, include_rohs=False)
            for h in hits:
                c.fail(item_key("ghs", h.code, h.message[:60]), h.message, waivable=False)
            c.evidence["ghs_constraints"] = {
                "forbidden_pictograms": ghs.forbidden_pictograms,
                "forbidden_signal_word": ghs.forbidden_signal_word,
                "voc_limit_g_l": ghs.voc_limit_g_l,
                "hits": len(hits),
            }
        except Exception as exc:
            c.fail("ghs_classification", f"无法分类，无法复核象形图/信号词/VOC：{exc}", waivable=False)


def check_ruleset_signoff(c: Check, rs) -> None:
    """Plan 1-1: released documents may only cite classification results from an
    EHS-signed-off rule set. Both halves are required: the rule-set file's
    ``status`` must be ``released`` *and* a valid sign-off record for that
    version must be filed. A draft rule set — or a released one without a
    record — fails the gate with an explicit "待 EHS 签核" message."""
    c.evidence["rule_set_status"] = rs.status
    if rs.status != "released":
        c.fail("ruleset_status",
               f"分类规则集 {rs.version} 状态为 {rs.status}，待 EHS 签核后方可用于已发布文档")
        return
    signoff = load_signoff(rs.version)
    if signoff is None:
        c.fail("ruleset_signoff",
               f"分类规则集 {rs.version} 已标记 released，但缺少 EHS 签核记录"
               f"（rulesets/signoffs/{rs.version}.yaml）")
        return
    for problem in signoff.problems(rs.version):
        c.fail("ruleset_signoff", problem)
    c.evidence["rule_set_signoff"] = {"signer": signoff.signer, "signed_at": signoff.signed_at}


def _check_classification(session: Session, c: Check, docs: list[RegulatoryDocumentRow], items: list[dict]) -> dict:
    """Recompute the classification the SDS / label were built from; returns ``{inputs_hash: result}``."""
    rs = load_ruleset()
    # Only documents that cite classification results are gated on the rule set.
    if any(d.doc_type in ("sds", "label") for d in docs):
        check_ruleset_signoff(c, rs)
    latest_release = ghs_service.latest_data_release(session)
    results: dict[str, dict] = {}
    hashes = set()
    for d in docs:
        if d.doc_type not in ("sds", "label"):
            continue
        meta = d.content.get("meta") or {}
        row = session.get(ClassificationResultRow, meta.get("classification_id") or "")
        name = f"{d.doc_type.upper()} {d.lang} v{d.version_no}"
        if row is None:
            c.fail(item_key(d.id), f"{name}：找不到生成时的分类结果")
            continue
        hashes.add(row.inputs_hash)
        results[row.inputs_hash] = row.result
        if row.rule_set_version != rs.version:
            c.fail(item_key(d.id, "ruleset"), f"{name}：分类规则集 {row.rule_set_version} 不是当前版本 {rs.version}")
        if latest_release and row.data_release_id != latest_release:
            c.fail(item_key(d.id, "release"), f"{name}：法规数据已更新（{row.data_release_id} → {latest_release}）")
        if items:
            fresh = ghs_service.classify_items(session, items, meta.get("classification_properties"))
            if fresh.inputs_hash != row.inputs_hash:
                c.fail(item_key(d.id, "inputs"), f"{name}：配方或物质数据已变化，分类结果需重新计算")
    if len(hashes) > 1:
        c.fail("mixed", "SDS / 标签基于不同的分类输入生成")
    c.evidence["rule_set_version"] = rs.version
    c.evidence["data_release_id"] = latest_release
    if c.items:
        c.notes.append("重新生成 SDS 与标签即可基于当前数据重新计算")
    return results


def _check_documents(c: Check, problems: list[tuple[str, str, str]]) -> None:
    for doc_type, lang, reason in problems:
        c.fail(item_key(doc_type, lang), f"{doc_type.upper()}（{lang}）：{reason}")


def _facts(d: RegulatoryDocumentRow) -> dict | None:
    return (d.content.get("meta") or {}).get("facts")


def _check_consistency(c: Check, docs: list[RegulatoryDocumentRow]) -> None:
    values: dict[str, list[tuple[str, float]]] = {}
    for d in docs:
        f = _facts(d)
        name = f"{d.doc_type.upper()} {d.lang}"
        if f is None:
            c.fail(item_key(d.id, "facts"), f"{name} v{d.version_no}：缺少可比对数据，请重新生成")
            continue
        for k, v in (f.get("properties") or {}).items():
            if k in CONSISTENCY_TOLERANCE and isinstance(v, (int, float)):
                values.setdefault(k, []).append((name, float(v)))
    for k, vals in values.items():
        lo, hi = min(vals, key=lambda x: x[1]), max(vals, key=lambda x: x[1])
        if hi[1] - lo[1] > CONSISTENCY_TOLERANCE[k] + 1e-9:  # binary floats: 1.36 - 1.34 > 0.02
            c.fail(item_key("value", k), f"{k}：{lo[0]} = {lo[1]:g}，{hi[0]} = {hi[1]:g}，超出容差 ±{CONSISTENCY_TOLERANCE[k]:g}")
    uns = {(f"SDS {d.lang}", (_facts(d) or {}).get("un_number")) for d in docs if d.doc_type == "sds" and _facts(d)}
    if len({u for _n, u in uns}) > 1:
        c.fail("un_number", "各语言 SDS 的 UN 号不一致：" + "，".join(f"{n} {u or '不受管制'}" for n, u in sorted(uns)))
    c.evidence["compared"] = {k: v for k, v in values.items()}


def _check_sds_label(c: Check, docs: list[RegulatoryDocumentRow]) -> None:
    sds = {d.lang: _facts(d) for d in docs if d.doc_type == "sds" and _facts(d)}
    labels = [(d.lang, _facts(d)) for d in docs if d.doc_type == "label" and _facts(d)]
    if not sds:
        c.notes.append("没有可比对的 SDS")
        return
    ref_lang = "zh-CN" if "zh-CN" in sds else next(iter(sds))
    ref = sds[ref_lang]
    for lang, f in sds.items():
        if lang == ref_lang:
            continue
        for k in ("pictograms", "signal_word", "h_codes"):
            if _norm(f.get(k)) != _norm(ref.get(k)):
                c.fail(item_key("sds", lang, k), f"SDS {lang} 与 SDS {ref_lang} 的 {k} 不一致")
    for lang, f in labels:
        s = sds.get(lang) or ref
        for k in ("pictograms", "signal_word", "h_codes"):
            if _norm(f.get(k)) != _norm(s.get(k)):
                c.fail(item_key("label", lang, k), f"标签 {lang} 的 {k}（{f.get(k)}）与 SDS 第 2 部分（{s.get(k)}）不一致")
        extra = sorted(set(f.get("p_codes") or []) - set(s.get("p_codes") or []))
        if extra:
            c.fail(item_key("label", lang, "p_codes"), f"标签 {lang} 的防范说明 {', '.join(extra)} 不在 SDS 第 2 部分中")


def _norm(v: Any) -> Any:
    return sorted(v) if isinstance(v, list) else v


def _check_fields(c: Check, docs: list[RegulatoryDocumentRow]) -> None:
    for d in docs:
        missing = (d.content.get("validation") or {}).get("missing") or []
        if missing:
            c.fail(item_key(d.id), f"{d.doc_type.upper()} {d.lang} v{d.version_no} 缺项：{'、'.join(missing)}")
        if d.doc_type == "sds" and not (d.content.get("header") or {}).get("revision_date"):
            c.fail(item_key(d.id, "revision_date"), f"SDS {d.lang} 缺少修订日期")


def _check_gaps(c: Check, results: dict[str, dict]) -> None:
    seen = set()
    for result in results.values():
        for gap in result.get("data_gaps") or []:
            k = item_key(gap.get("code"), hashlib.sha1((gap.get("message") or "").encode()).hexdigest()[:12])
            if k in seen:
                continue
            seen.add(k)
            blocking = gap.get("severity") == "blocking"
            c.fail(k, ("【阻断】" if blocking else "") + (gap.get("message") or gap.get("code") or ""),
                   waivable=not blocking)


def waivers_for(session: Session, revision_id: str) -> dict[tuple[str, str, str | None], ReleaseGateWaiverRow]:
    """Waivers keyed by (check_code, item_key, content_hash).

    Pre-0067 rows have content_hash NULL and therefore never match a current
    item message — they are intentionally invalidated by the binding change.
    """
    return {(w.check_code, w.item_key, w.content_hash): w for w in session.execute(
        select(ReleaseGateWaiverRow).where(ReleaseGateWaiverRow.revision_id == revision_id)).scalars()}


def evaluate(session: Session, revision_id: str) -> dict[str, Any]:
    rev = session.get(ProductRevisionRow, revision_id)
    if rev is None:
        raise GateError("产品修订版不存在")
    product = session.get(ProductRow, rev.product_id)
    checks = {key: Check(key, code, title) for key, code, title in CHECKS}
    fv, items = _items(session, rev)
    _check_total(checks["formula_total"], fv)
    components = _check_substances(session, checks["substance_data"], items)
    _check_objectives(session, checks["objectives"], rev)
    _check_red_lines(session, checks["red_lines"], rev, fv, components, items)
    docs, problems = select_package_documents(session, rev)
    results = _check_classification(session, checks["classification_current"], docs, items)
    _check_documents(checks["documents"], problems)
    _check_consistency(checks["consistency"], docs)
    _check_sds_label(checks["sds_label"], docs)
    _check_fields(checks["required_fields"], docs)
    _check_gaps(checks["data_gaps"], results)

    waivers = waivers_for(session, rev.id)
    out = [checks[key].to_dict(waivers) for key, _c, _t in CHECKS]
    for o, (key, _c, _t) in zip(out, CHECKS, strict=True):
        if checks[key].not_applicable and o["status"] == "pass":
            o["status"] = "not_applicable"
    passed = all(o["status"] in ("pass", "waived", "not_applicable") for o in out)
    batches = {d.batch_id for d in docs if d.batch_id}
    return {
        "revision_id": rev.id, "product_code": product.code if product else None, "rev": rev.rev,
        "passed": passed, "evaluated_at": utcnow().isoformat(), "checks": out,
        "documents": [{"id": d.id, "doc_type": d.doc_type, "lang": d.lang, "version_no": d.version_no,
                       "status": d.status, "batch_id": d.batch_id} for d in docs],
        "batches": sorted(b for b in batches if session.get(BatchRow, b) is not None),
    }


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """The part of a gate result that is frozen into the release package (no timestamps)."""
    return {"passed": result["passed"],
            "checks": [{"code": c["code"], "key": c["key"], "status": c["status"],
                        "waivers": sorted(i["waiver"]["id"] for i in c["items"] if i.get("waiver"))}
                       for c in result["checks"]]}
