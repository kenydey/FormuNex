"""Chat / retrieval DTOs: evidence, passages, source guides, chunk responses."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator



class EvidenceRef(BaseModel):
    """v20-1: 引用可解释性 —— 每条引用附带命中原因。

    前端用此展示"为什么引用这条证据"。
    """

    id: str  # 如 "EV1"
    hit_type: str  # "verbatim" | "cas" | "token" | "semantic"
    hit_detail: str = ""  # 如 "逐字命中 '磷酸锌 10%'", "CAS 7779-90-0"
    confidence: str = "high"

EntityKind = Literal["chemical", "trade_product", "element", "parameter"]

CompositionStatus = Literal["resolved", "partial", "mixture", "proprietary", "unknown"]

class EvidenceEntityRef(BaseModel):
    """KG entity linked to a retrieved evidence chunk."""

    entity_id: str
    kind: EntityKind
    display_name: str
    composition_status: CompositionStatus = "unknown"
    surface_form: str | None = None

class Evidence(BaseModel):
    """A retrieved patent or literature snippet with a citation."""

    source: str  # USPTO / EPO / literature / seed / surechembl
    identifier: str
    title: str
    snippet: str
    relevance: float = Field(ge=0, le=1)
    is_seed_corpus: bool = False
    entity_refs: list[EvidenceEntityRef] = Field(default_factory=list)
    # Open Access metadata carried from the search tier so the full-text fetcher
    # can skip non-OA literature with zero extra API calls.
    oa_pdf_url: str | None = None
    is_oa: bool | None = None
    # Optional landing URL (e.g. Google Patents for SureChEMBL SCPN).
    url: str | None = None
    # Secondary UI link (e.g. SureChEMBL document page).
    url_alt: str | None = None
    # SureChEMBL / patent metadata for KG ingest + draft review (P3).
    assignee: str | None = None
    pub_date: str | None = None
    # P0 domain-profile audit fields (optional; old clients ignore).
    domain_tags: list[str] = Field(default_factory=list)
    domain_match: Literal["strong", "weak", "none"] | None = None
    taxonomy_source: Literal["arxiv", "openalex", "chemrxiv", "cpc", "lexical", "none"] | None = None
    # P1 #16: page/paragraph anchors for citation lines (align CitationAnchor).
    page: int | None = None
    # PageIndex 借鉴 A5: 跨页末页（None = 单页，与 page 相同）。
    page_end: int | None = None
    paragraph: int | None = None
    # W2-2 (P1-7): two-stage search→read — whether the full text is already
    # persisted locally (read stage can skip the download). None = unknown.
    has_fulltext: bool | None = None
    # W4-6 (P0-19): citation count + publication year for age-normalized
    # ranking (new papers not drowned by old ones). None = unknown.
    cited_by: int | None = None
    pub_year: int | None = None

class Passage(BaseModel):
    """A page-anchored full-text passage from the read stage (W2-2 / P1-7)."""

    source_id: str = ""
    page_no: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    section_title: str = ""
    text: str = ""
    # W3-3 (P1-24): how the passage text was obtained — "enrich" when
    # fulltext_enrich already had the full text (reused, no new fetch),
    # "ondemand" when this read call fetched it (or pulled a pre-existing
    # local document from the store outside the enrich pipeline).
    origin: str = ""

class ParameterBoundary(BaseModel):
    min_value: float | None = Field(None, description="最小值")
    max_value: float | None = Field(None, description="最大值")
    unit: str = Field(..., description="单位，如 g/L, °C, % 等")

    @model_validator(mode="after")
    def check_physical_laws(self) -> "ParameterBoundary":
        lo, hi = self.min_value, self.max_value
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"物理定律冲突: {lo} > {hi}")
        if lo is not None and lo < 0 and self.unit.strip() in ("g/L", "mg/L", "kg", "%", "wt%"):
            raise ValueError(f"浓度不能为负: {lo}{self.unit}")
        return self

class ProductMention(BaseModel):
    """A commercial chemical product mentioned in a document."""

    trade_name: str = Field(..., max_length=120)
    grade: str = Field(default="", max_length=60)
    supplier: str = Field(default="", max_length=120)
    generic_name: str = Field(default="", max_length=200, description="对应通用化学名")
    role: str = Field(default="", max_length=60, description="树脂/固化剂/颜料/助剂…")

class SourceGuideSchema(BaseModel):
    summary: str = Field(..., max_length=600, description="300字内核心化学机理与工艺摘要")
    key_entities: list[str] = Field(..., min_length=1, max_length=30)
    parameter_space: dict[str, ParameterBoundary] = Field(default_factory=dict)
    faqs: list[str] = Field(..., min_length=1, max_length=5)
    products: list[ProductMention] = Field(default_factory=list, max_length=30)
    status: Literal["verified", "degraded"] = "verified"

    @model_validator(mode="after")
    def _trim_summary(self) -> "SourceGuideSchema":
        if len(self.summary) > 300:
            self.summary = self.summary[:300]
        return self

    @classmethod
    def degraded(cls, reason: str = "提取失败") -> "SourceGuideSchema":
        return cls(
            summary=reason,
            key_entities=["未知"],
            parameter_space={},
            faqs=["该文献结构化提取未成功，请人工审核"],
            status="degraded",
        )

class DocumentChunkResponse(BaseModel):
    """A single document chunk with page/paragraph/offset provenance."""

    id: str
    source_id: str
    ord: int
    text: str
    heading_path: str = ""
    page: int | None = None
    paragraph: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    meta: dict | None = None

class ChunkListResponse(BaseModel):
    """Wrapper for chunk retrieval responses.

    P1-2: ``total`` is the full chunk count for the source, independent of
    the page window (``limit``/``offset``). Callers must page until
    ``len(chunks)`` reaches ``total`` — a first page alone is a truncation,
    not the whole document.
    """

    chunks: list[DocumentChunkResponse]
    total: int = 0
