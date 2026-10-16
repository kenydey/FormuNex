"""Structured document content shared by SDS / TDS / COA / label.

``content_json`` (stored on ``regulatory_documents``) is the source of truth;
PDF and Word are renderings of it. Its shape is the same for every document
type so one renderer, one diff and one consistency check serve them all::

    {"doc_type": "sds", "lang": "zh-CN", "title": "...", "header": {...},
     "sections": [{"no": 1, "key": "identification", "title": "...",
                   "fields": [{"key", "label", "value", "source"}],
                   "tables": [{"key", "columns": [...], "rows": [[...]]}],
                   "paragraphs": ["..."]}],
     "validation": {"missing": [...], "warnings": [...]}}

Every field records where its value came from — ``computed`` (GB 30000
engine), ``measured``, ``product`` / ``company`` (master data), ``manual``,
``template`` (standard wording) — which is what the proofreading screen shows
and what the diff weighs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

NO_DATA = {"zh-CN": "无资料", "en-US": "No data available"}
NOT_APPLICABLE = {"zh-CN": "不适用", "en-US": "Not applicable"}


def t(lang: str, zh: str, en: str) -> str:
    return en if lang == "en-US" else zh


@dataclass
class Section:
    no: int
    key: str
    title: str
    fields: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    def add(self, key: str, label: str, value: Any, source: str) -> None:
        self.fields.append({"key": key, "label": label, "value": value, "source": source})

    def table(self, key: str, columns: list[str], rows: list[list[Any]], source: str = "computed") -> None:
        self.tables.append({"key": key, "columns": columns, "rows": rows, "source": source})

    def to_dict(self) -> dict:
        return {"no": self.no, "key": self.key, "title": self.title, "fields": self.fields,
                "tables": self.tables, "paragraphs": self.paragraphs}


@dataclass
class DocumentContent:
    doc_type: str
    lang: str
    title: str
    header: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def section(self, no: int, key: str, title: str) -> Section:
        s = Section(no, key, title)
        self.sections.append(s)
        return s

    def require(self, value: Any, what: str) -> Any:
        if value in (None, "", []):
            self.missing.append(what)
        return value

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type, "lang": self.lang, "title": self.title, "header": self.header,
            "sections": [s.to_dict() for s in self.sections],
            "validation": {"missing": self.missing, "warnings": self.warnings}, "meta": self.meta,
        }


def field_sources(content: dict) -> dict[str, str]:
    """Flat ``{"section.field": source}`` map stored next to the content."""
    out: dict[str, str] = {}
    for s in content.get("sections", []):
        for f in s.get("fields", []):
            out[f"{s['key']}.{f['key']}"] = f.get("source", "")
        for tb in s.get("tables", []):
            out[f"{s['key']}.{tb['key']}"] = tb.get("source", "")
    return out


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def field_value(content: dict, section_key: str, field_key: str) -> Any:
    for s in content.get("sections", []):
        if s.get("key") == section_key:
            for f in s.get("fields", []):
                if f.get("key") == field_key:
                    return f.get("value")
    return None
