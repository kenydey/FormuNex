"""Compile SourceDocument facts into LLM Wiki material/chemical pages (W2).

Deterministic merge (no LLM rewrite in this slice): union source_ids and append
``### Source`` evidence blocks from SourceGuide / chunk chem meta / kb_products.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from ...config import get_settings
from ...db.chunk_store import get_chunk_store
from ...db.material_store import norm_key as material_norm_key
from ...db.source_store import get_source_store
from ...db.wiki_store import WikiProjectCollisionError, get_wiki_store
from .schema import (
    chemical_entity_id,
    chemical_path,
    dump_page,
    extract_evidence_blocks,
    material_entity_id,
    material_path,
    mechanism_path,
    parse_front_matter,
    pitfall_path,
    safe_key,
    system_path,
    upsert_source_block,
)

logger = logging.getLogger(__name__)

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def compile_source(source_id: str, *, source_hash: str | None = None) -> dict[str, Any]:
    """Merge one ingested source into wiki pages. Never raises to callers.

    ``source_hash`` (批 6) overrides the recorded propagation key: pass
    the hash of the new content when the caller just re-indexed the
    source, because ``SourceDocument.content_hash`` is only written at
    create/revive time and goes stale on ``index_source`` re-index
    (it is provenance write-protected, see ``source_store.update_fields``).
    When omitted, the stored row hash is used, falling back to a hash of
    the current chunk texts.
    """
    try:
        return _compile_source_impl(source_id, source_hash=source_hash)
    except Exception as exc:  # noqa: BLE001 — compile must never break ingest
        logger.exception("wiki compile_source failed for %s: %s", source_id, exc)
        return {"ok": False, "reason": "error", "error": str(exc), "pages": []}


def _compile_source_impl(source_id: str, *, source_hash: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.wiki_enabled:
        return {"ok": False, "reason": "wiki_disabled", "pages": []}

    sid = (source_id or "").strip()
    if not sid:
        return {"ok": False, "reason": "missing_source_id", "pages": []}

    store = get_wiki_store()
    sources = get_source_store()
    doc = sources.get(sid)
    if doc is None:
        return {"ok": False, "reason": "source_not_found", "pages": []}
    # P0-2: stamp compiled pages with the source's project for isolation.
    doc_project_id = getattr(doc, "project_id", None) or None

    guide = sources.get_source_guide(sid)
    chunks = get_chunk_store().get_by_source(sid)

    # 批 5-3：源内容 hash（SourceDocument.content_hash；缺失时退化为
    # chunk 文本 hash）。drift 开关只决定"hash 不同时是否刷新"，hash
    # 行本身新建块即记录，供日后启用时比对。批 6：调用方传入的
    # source_hash 优先——重入库后的行 hash 已 stale，不能再用它当新
    # 内容的传播键（行 hash 是来源写保护字段，不由失效路径改写）。
    source_hash = (source_hash or "").strip()
    if not source_hash:
        source_hash = (getattr(doc, "content_hash", "") or "").strip()
    if not source_hash:
        source_hash = hashlib.sha256(
            "\n".join((getattr(c, "text", "") or "") for c in (chunks or [])).encode(
                "utf-8"
            )
        ).hexdigest()
    drift_enabled = bool(settings.wiki_drift_refresh_enabled)

    materials: dict[str, dict[str, Any]] = {}
    chemicals: dict[str, dict[str, Any]] = {}

    def _add_material(name: str, *, grade: str = "", supplier: str = "", role: str = "", note: str = "") -> None:
        title = (name or "").strip()
        if not title:
            return
        key = safe_key(material_norm_key(title))
        slot = materials.setdefault(
            key,
            {
                "title": title,
                "norm_key": key,
                "grade": grade,
                "supplier": supplier,
                "role": role,
                "notes": [],
            },
        )
        if grade and not slot.get("grade"):
            slot["grade"] = grade
        if supplier and not slot.get("supplier"):
            slot["supplier"] = supplier
        if role and not slot.get("role"):
            slot["role"] = role
        if note:
            slot["notes"].append(note)

    def _add_cas(cas: str, *, note: str = "") -> None:
        c = (cas or "").strip()
        if not _CAS_RE.match(c):
            return
        slot = chemicals.setdefault(
            c,
            {"cas": c, "title": c, "notes": []},
        )
        if note:
            slot["notes"].append(note)

    if guide is not None:
        for p in guide.products or []:
            _add_material(
                p.trade_name or p.generic_name or "",
                grade=p.grade or "",
                supplier=p.supplier or "",
                role=p.role or "",
                note="from source_guide.products",
            )
        for ent in guide.key_entities or []:
            if _CAS_RE.match(str(ent).strip()):
                _add_cas(str(ent), note="from source_guide.key_entities")
            elif len(str(ent).strip()) >= 2:
                _add_material(str(ent), note="from source_guide.key_entities")

    for ch in chunks or []:
        meta = getattr(ch, "meta", None) or {}
        for p in meta.get("products") or []:
            _add_material(
                p.get("trade_name") or "",
                grade=p.get("grade") or "",
                supplier=p.get("supplier") or "",
                note="from chunk chem_extract",
            )
        for c in meta.get("chem") or []:
            if (c.get("type") or "").lower() == "cas":
                _add_cas(str(c.get("value") or ""), note="from chunk chem_extract")

    # Also pull kb_products that list this source
    try:
        from ...db.product_store import get_product_store

        for prod in get_product_store().search("", limit=200):
            sids = list(prod.source_ids or [])
            if sid not in sids:
                continue
            _add_material(
                prod.trade_name or "",
                grade=prod.grade or "",
                supplier=prod.supplier or "",
                role=prod.role or "",
                note="from kb_products registry",
            )
            if prod.cas:
                _add_cas(prod.cas, note=f"linked product {prod.trade_name}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki compile: product_store scan skipped: %s", exc)

    updated: list[str] = []
    title_hint = (doc.title or doc.filename or sid)[:120]

    for key, info in materials.items():
        path = material_path(key)
        entity_id = material_entity_id(key)
        existing_md = store.read_markdown(path)
        source_ids = [sid]
        flags: list[str] = []
        evidence: list[str] = []
        summary = f"Commercial / named material **{info['title']}**."
        if info.get("grade"):
            summary += f" Grade: {info['grade']}."
        if info.get("supplier"):
            summary += f" Supplier: {info['supplier']}."
        if info.get("role"):
            summary += f" Role: {info['role']}."
        fresh_summary = summary

        if existing_md:
            meta, body = parse_front_matter(existing_md)
            source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
            flags = list(meta.get("flags") or [])
            evidence = extract_evidence_blocks(body)
            # Keep prior summary paragraph if present
            if "## Summary" in body:
                prev = body.split("## Summary", 1)[1]
                if "## Evidence" in prev:
                    prev = prev.split("## Evidence", 1)[0]
                prev = prev.strip()
                if prev and prev != "_No summary yet._":
                    summary = prev

        lines = [
            f"### Source `{sid}`",
            f"- document: {title_hint}",
            f"- origin: {doc.origin_url or ''}",
        ]
        for n in info.get("notes") or []:
            lines.append(f"- note: {n}")
        evidence, _refreshed = upsert_source_block(
            evidence, sid, lines, source_hash, drift_enabled=drift_enabled
        )
        if _refreshed:
            # 批 5-3：源内容变了，Summary 按当前输入重算，不沿用旧摘要。
            summary = fresh_summary

        md = dump_page(
            kind="material",
            title=info["title"],
            entity_id=entity_id,
            norm_key=key,
            source_ids=source_ids,
            flags=flags,
            summary=summary,
            evidence_blocks=evidence,
        )
        try:
            row = store.upsert_page(
                path=path,
                project_id=doc_project_id,
                kind="material",
                title=info["title"],
                norm_key=key,
                entity_id=entity_id,
                markdown=md,
                source_ids=source_ids,
                flags=flags,
            )
        except WikiProjectCollisionError as exc:
            logger.warning("wiki compile skipped page on project collision: %s", exc)
            continue
        updated.append(row.path)

    for cas, info in chemicals.items():
        path = chemical_path(cas=cas)
        entity_id = chemical_entity_id(cas=cas)
        existing_md = store.read_markdown(path)
        source_ids = [sid]
        flags: list[str] = []
        evidence: list[str] = []
        summary = f"Chemical entity CAS **{cas}**."
        fresh_summary = summary

        if existing_md:
            meta, body = parse_front_matter(existing_md)
            source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
            flags = list(meta.get("flags") or [])
            evidence = extract_evidence_blocks(body)
            if "## Summary" in body:
                prev = body.split("## Summary", 1)[1]
                if "## Evidence" in prev:
                    prev = prev.split("## Evidence", 1)[0]
                prev = prev.strip()
                if prev and prev != "_No summary yet._":
                    summary = prev

        lines = [
            f"### Source `{sid}`",
            f"- document: {title_hint}",
            f"- origin: {doc.origin_url or ''}",
        ]
        for n in info.get("notes") or []:
            lines.append(f"- note: {n}")
        evidence, _refreshed = upsert_source_block(
            evidence, sid, lines, source_hash, drift_enabled=drift_enabled
        )
        if _refreshed:
            # 批 5-3：源内容变了，Summary 按当前输入重算，不沿用旧摘要。
            summary = fresh_summary

        md = dump_page(
            kind="chemical",
            title=info["title"],
            entity_id=entity_id,
            norm_key=cas,
            source_ids=source_ids,
            flags=flags,
            summary=summary,
            evidence_blocks=evidence,
        )
        try:
            row = store.upsert_page(
                path=path,
                project_id=doc_project_id,
                kind="chemical",
                title=info["title"],
                norm_key=cas,
                entity_id=entity_id,
                markdown=md,
                source_ids=source_ids,
                flags=flags,
            )
        except WikiProjectCollisionError as exc:
            logger.warning("wiki compile skipped page on project collision: %s", exc)
            continue
        updated.append(row.path)

    # Systems / mechanisms / pitfalls from SourceGuide (W4)
    if guide is not None:
        for pname, bound in (guide.parameter_space or {}).items():
            key = safe_key(pname)
            path = system_path(pname)
            bounds = [
                {
                    "name": pname,
                    "min": bound.min_value,
                    "max": bound.max_value,
                    "unit": bound.unit or "",
                }
            ]
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags: list[str] = []
            evidence: list[str] = []
            summary = f"Process / DOE parameter **{pname}** from literature guides."
            forbidden: list[str] = []
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
                import json as _json

                raw_b = meta.get("bounds_json")
                if isinstance(raw_b, str):
                    try:
                        prev = _json.loads(raw_b)
                        if isinstance(prev, list):
                            # merge by name
                            by = {str(x.get("name")): x for x in prev if isinstance(x, dict)}
                            by[pname] = bounds[0]
                            bounds = list(by.values())
                    except Exception:
                        pass
            evidence, _refreshed = upsert_source_block(
                evidence,
                sid,
                [
                    f"### Source `{sid}`",
                    f"- document: {title_hint}",
                    f"- parameter_space: {pname} "
                    f"[{bound.min_value}–{bound.max_value} {bound.unit}]",
                ],
                source_hash,
                drift_enabled=drift_enabled,
            )
            # Tight max as soft forbidden note
            if bound.max_value is not None:
                forbidden.append(f"{pname} > {bound.max_value} {bound.unit}".strip())
            try:
                row = store.upsert_page(
                    path=path,
                    project_id=doc_project_id,
                    kind="system",
                    title=pname,
                    norm_key=key,
                    entity_id=f"system:{key}"[:64],
                    markdown=dump_page(
                        kind="system",
                        title=pname,
                        entity_id=f"system:{key}"[:64],
                        norm_key=key,
                        source_ids=source_ids,
                        flags=flags,
                        summary=summary,
                        evidence_blocks=evidence,
                        bounds=bounds,
                        forbidden=forbidden,
                    ),
                    source_ids=source_ids,
                    flags=flags,
                )
            except WikiProjectCollisionError as exc:
                logger.warning("wiki compile skipped page on project collision: %s", exc)
                continue
            updated.append(row.path)

        # Mechanism page from summary
        if (guide.summary or "").strip() and guide.status != "degraded":
            mkey = safe_key((guide.key_entities or ["mechanism"])[0])[:80]
            path = mechanism_path(mkey)
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags = []
            evidence = []
            summary = guide.summary.strip()
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
                if "## Summary" in body:
                    prev = body.split("## Summary", 1)[1]
                    if "## Evidence" in prev:
                        prev = prev.split("## Evidence", 1)[0]
                    prev = prev.strip()
                    if prev and prev != "_No summary yet._" and summary not in prev:
                        summary = f"{prev}\n\n---\n\n{summary}"
            evidence, _refreshed = upsert_source_block(
                evidence,
                sid,
                [
                    f"### Source `{sid}`",
                    f"- document: {title_hint}",
                    "- note: from source_guide.summary",
                ],
                source_hash,
                drift_enabled=drift_enabled,
            )
            try:
                row = store.upsert_page(
                    path=path,
                    project_id=doc_project_id,
                    kind="mechanism",
                    title=f"机理 · {mkey}",
                    norm_key=mkey,
                    entity_id=f"mechanism:{mkey}"[:64],
                    markdown=dump_page(
                        kind="mechanism",
                        title=f"机理 · {mkey}",
                        entity_id=f"mechanism:{mkey}"[:64],
                        norm_key=mkey,
                        source_ids=source_ids,
                        flags=flags,
                        summary=summary,
                        evidence_blocks=evidence,
                    ),
                    source_ids=source_ids,
                    flags=flags,
                )
            except WikiProjectCollisionError as exc:
                logger.warning("wiki compile skipped page on project collision: %s", exc)
                row = None
            if row is not None:
                updated.append(row.path)

        # Pitfalls from FAQs mentioning 避免/禁忌/不要/禁止/avoid
        _pit_re = re.compile(r"避免|禁忌|不要|禁止|avoid|forbid|never", re.I)
        for faq in guide.faqs or []:
            text = (faq or "").strip()
            if not text or not _pit_re.search(text):
                continue
            pkey = safe_key(text)[:80]
            path = pitfall_path(pkey)
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags = []
            evidence = []
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
            evidence, _refreshed = upsert_source_block(
                evidence,
                sid,
                [
                    f"### Source `{sid}`",
                    f"- document: {title_hint}",
                    f"- faq: {text}",
                ],
                source_hash,
                drift_enabled=drift_enabled,
            )
            try:
                row = store.upsert_page(
                    path=path,
                    project_id=doc_project_id,
                    kind="pitfall",
                    title=text[:120],
                    norm_key=pkey,
                    entity_id=f"pitfall:{pkey}"[:64],
                    markdown=dump_page(
                        kind="pitfall",
                        title=text[:120],
                        entity_id=f"pitfall:{pkey}"[:64],
                        norm_key=pkey,
                        source_ids=source_ids,
                        flags=flags,
                        summary=text,
                        evidence_blocks=evidence,
                        forbidden=[text],
                    ),
                    source_ids=source_ids,
                    flags=flags,
                )
            except WikiProjectCollisionError as exc:
                logger.warning("wiki compile skipped page on project collision: %s", exc)
                continue
            updated.append(row.path)

    # Post: lint + optional neo4j
    if settings.wiki_lint_on_compile and updated:
        try:
            from .lint import lint_paths

            lint_paths(updated)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki lint after compile skipped: %s", exc)
    if settings.wiki_neo4j_project and updated:
        try:
            from .neo4j_project import project_paths

            project_paths(updated)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki neo4j project skipped: %s", exc)

    return {
        "ok": True,
        "source_id": sid,
        "pages": updated,
        "materials": len(materials),
        "chemicals": len(chemicals),
    }
