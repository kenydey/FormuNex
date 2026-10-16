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
