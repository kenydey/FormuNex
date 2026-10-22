"""Product lifecycle, regulatory documents, release and audit (alembic 0055-0057).

State lives in rows, rendered files live in object storage (``*_key`` columns).
Structured ``content_json`` is the source of truth for every regulatory
document: diff, proofreading and cross-document consistency checks all work on
it, and PDF / Word are renderings of it.

Immutability rules (enforced in services, and for ``audit_log`` by a database
trigger): a frozen product revision, a released document and an audit row are
never edited — a change is a new row.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _utcnow

_json = JSON().with_variant(JSONB(), "postgresql")


class ProductRow(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_zh: Mapped[str] = mapped_column(String(200), default="")
    name_en: Mapped[str] = mapped_column(String(200), default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # concept | rd | candidate | under_review | released | superseded | obsolete
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="concept", index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ProductRevisionRow(Base):
    __tablename__ = "product_revisions"
    __table_args__ = (UniqueConstraint("product_id", "rev", name="uq_product_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    rev: Mapped[str] = mapped_column(String(8))  # A, B, C ...
    formulation_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("formulation_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    winning_experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spec_snapshot: Mapped[dict] = mapped_column(_json, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ClassificationResultRow(Base):
    """A GB 30000 mixture classification, pinned to the rules and data used."""

    __tablename__ = "classification_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    formulation_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    rule_set_version: Mapped[str] = mapped_column(String(40))
    data_release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    inputs_hash: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[dict] = mapped_column(_json, default=dict)
    trace: Mapped[list] = mapped_column(_json, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LineageEdgeRow(Base):
    """One hop of the R&D → PLM data chain (source → spec → formula → ... → release)."""

    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint("from_type", "from_id", "to_type", "to_id", "relation", name="uq_lineage_edge"),
        Index("ix_lineage_to", "to_type", "to_id"),
        Index("ix_lineage_from", "from_type", "from_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_type: Mapped[str] = mapped_column(String(32))
    from_id: Mapped[str] = mapped_column(String(64))
    to_type: Mapped[str] = mapped_column(String(32))
    to_id: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DocTemplateRow(Base):
    __tablename__ = "doc_templates"
    __table_args__ = (
        UniqueConstraint("doc_type", "lang", "name", "version", name="uq_doc_template"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)  # sds | tds | coa | label
    lang: Mapped[str] = mapped_column(String(8), default="zh-CN")
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    engine: Mapped[str] = mapped_column(String(16), default="html")  # html | docx | label_json
    content_key: Mapped[str] = mapped_column(String(300), default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class RegulatoryDocumentRow(Base):
    __tablename__ = "regulatory_documents"
    __table_args__ = (
        UniqueConstraint("revision_id", "doc_type", "lang", "version_no", name="uq_regulatory_document"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # COA only
    doc_type: Mapped[str] = mapped_column(String(16), index=True)
    lang: Mapped[str] = mapped_column(String(8), default="zh-CN")
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    # draft | in_review | approved | released | superseded
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content: Mapped[dict] = mapped_column(_json, default=dict)
    field_sources: Mapped[dict] = mapped_column(_json, default=dict)
    rendered_pdf_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    rendered_docx_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BatchRow(Base):
    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("product_id", "batch_no", name="uq_batch"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    batch_no: Mapped[str] = mapped_column(String(64))
    produced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quantity_kg: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | released | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BatchQcResultRow(Base):
    __tablename__ = "batch_qc_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(80))
    value: Mapped[float | None] = mapped_column(nullable=True)
    value_text: Mapped[str] = mapped_column(String(120), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    test_method: Mapped[str] = mapped_column(String(80), default="")
    spec_min: Mapped[float | None] = mapped_column(nullable=True)
    spec_max: Mapped[float | None] = mapped_column(nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)  # PASS | FAIL
    measurement_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ReleasePackageRow(Base):
    __tablename__ = "release_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="CASCADE"), index=True
    )
    gate_result: Mapped[dict] = mapped_column(_json, default=dict)
    document_ids: Mapped[list] = mapped_column(_json, default=list)
    package_hash: Mapped[str] = mapped_column(String(64), default="")
    # draft | gate_failed | under_review | rejected | released | withdrawn
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # alembic 0060: the delivery ZIP (documents + manifest) and per-connector publish outcomes.
    delivery_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    publish_results: Mapped[list] = mapped_column(_json, default=list)


class ReleaseGateWaiverRow(Base):
    """An approved exception to one waivable release-gate item (alembic 0060).

    Only gate checks the plan marks waivable (objective targets, data gaps) accept
    one; every waiver is e-signed and audited and shows up in the gate result.

    The waiver is bound to the exact failure text it waives: ``content_hash``
    is the sha256 of the gate item message at waiver time (alembic 0067), and
    evaluation only honours a waiver whose hash still matches the current
    message. When the underlying limit or measured value changes, the message
    changes, the old waiver stops matching, and the item fails again until
    re-waived. Rows written before 0067 have NULL content_hash and therefore
    never match — historical waivers are intentionally invalidated by the
    binding change (EHS must be told, see the P0-2 fix note).
    """

    __tablename__ = "release_gate_waivers"
    __table_args__ = (UniqueConstraint("revision_id", "check_code", "item_key", "content_hash",
                                       name="uq_gate_waiver_content"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="CASCADE"), index=True
    )
    check_code: Mapped[str] = mapped_column(String(32))
    item_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    waived_by: Mapped[str] = mapped_column(String(36))
    signature_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ApprovalFlowRow(Base):
    __tablename__ = "approval_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32))  # release_package | regulatory_document | ...
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    template: Mapped[str] = mapped_column(String(40), default="release_4_step")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApprovalStepRow(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (UniqueConstraint("flow_id", "seq", name="uq_approval_step"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flow_id: Mapped[str] = mapped_column(ForeignKey("approval_flows.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(String(40))
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # pending | approved | rejected | skipped
    status: Mapped[str] = mapped_column(String(16), default="pending")
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ESignatureRow(Base):
    __tablename__ = "e_signatures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    step_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    meaning: Mapped[str] = mapped_column(String(16))  # author | review | approve | release
    signed_hash: Mapped[str] = mapped_column(String(64))
    reauth_method: Mapped[str] = mapped_column(String(16), default="password")
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditLogRow(Base):
    """Append-only, hash-chained audit trail (UPDATE / DELETE blocked by trigger)."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class DocumentTocTreeRow(Base):
    """PageIndex hierarchical table-of-contents tree for one source document."""

    __tablename__ = "document_toc_trees"

    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tree: Mapped[dict] = mapped_column(_json, default=dict)
    builder_version: Mapped[str] = mapped_column(String(16), default="1")
    doc_kind: Mapped[str] = mapped_column(String(16), default="generic")  # patent | standard | paper | generic
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    max_depth: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # none | pending | partial | done
    summaries_status: Mapped[str] = mapped_column(String(16), default="none")
    built_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# --------------------------------------------------------------------------- 整合方案 P0 (alembic 0064)


class CompanyRow(Base):
    """One legal entity whose name / address / emergency phone lands on SDS §1, TDS and labels.

    Documents read the *default* company at generation time; a generated
    document keeps the company it was built with (its content is immutable).
    Companies are never deleted — supersede by creating a new default — so
    issued documents always resolve to a real record.
    """

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    address_en: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    fax: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    emergency_phone: Mapped[str] = mapped_column(String(64))  # 24h 应急电话，SDS 强制非空
    unified_credit_code: Mapped[str] = mapped_column(String(32), default="")
    logo_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TsTemplateRow(Base):
    """A TS template by product category (e.g. 涂料、清洗剂、转化膜).

    Seeds the indicator rows of a generated TS; the template itself carries
    NO spec limits — ``indicators_json`` holds indicator *structure*
    (name / unit / test method / factory-inspection flag) with empty limits,
    which quality fills in per product. Never fabricate limits.
    """

    __tablename__ = "ts_templates"
    __table_args__ = (UniqueConstraint("category", "name", "version", name="uq_ts_template"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    sections_json: Mapped[dict] = mapped_column(_json, default=dict)  # chapter structure (titles / notes)
    indicators_json: Mapped[list] = mapped_column(_json, default=list)  # default indicator rows (limits empty)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BatchExportRow(Base):
    """One batch document export: multi-product × multi-doc-type → one ZIP.

    The ZIP (rendered files + manifest.json) is stored under ``result_key``;
    the row records what was requested and what was missing, so a partial
    export is visible instead of silently short.
    """

    __tablename__ = "batch_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # pending | done | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    product_ids: Mapped[list] = mapped_column(_json, default=list)
    doc_types: Mapped[list] = mapped_column(_json, default=list)
    lang: Mapped[str] = mapped_column(String(8), default="zh-CN")
    filename_template: Mapped[str] = mapped_column(String(300), default="")
    include_docx: Mapped[bool] = mapped_column(default=False)
    result_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    missing: Mapped[list] = mapped_column(_json, default=list)  # [{product_code, doc_type, reason}]
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class FormulaMappingRow(Base):
    """Mapping between an MSDS-Generator formula record and FormuNex entities.

    The external record (company + product + formula) maps to a
    ``FormulationVersion`` (snapshot carries ``source: "msds_generator"``)
    and, when a FormuNex product exists for it, to that product. The mapping
    itself is the association — it never rewrites ``FormulationVersion``
    semantics; a ``ProductRevision`` is created on demand from a mapping.
    """

    __tablename__ = "formula_mappings"
    __table_args__ = (Index("ix_formula_mappings_lookup", "product_id", "source", "source_ref"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    formulation_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("formulation_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="msds_generator", index=True)
    source_ref: Mapped[str] = mapped_column(String(200), default="")  # external record key
    source_payload: Mapped[dict] = mapped_column(_json, default=dict)  # the external record, verbatim
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
