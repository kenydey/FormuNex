"""Built-in lab execution module (alembic 0059, plan v1.1 §5.3.1).

FormuNex's own PostgreSQL is the system of record for experiment execution:
DOE runs become lab tasks, physical specimens are samples, every number in
``measurements`` points at a test method and instrument and is reviewed by a
second person before optimisation, SDS section 9 or a COA may use it. External
ELNs (eLabFTW, Datalab, commercial) sync through ``eln_links``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _utcnow


class TestMethodRow(Base):
    """Method library: a measurement without a method is not a result."""

    __tablename__ = "test_methods"
    __test__ = False  # not a pytest class, despite the name

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # "GB/T 10125-2021"
    name_zh: Mapped[str] = mapped_column(String(200), default="")
    name_en: Mapped[str] = mapped_column(String(200), default="")
    metric: Mapped[str] = mapped_column(String(80), default="", index=True)
    unit: Mapped[str] = mapped_column(String(32), default="")
    # GB/T 8170 rounding: number of decimals or significant figures.
    rounding: Mapped[dict] = mapped_column(JSON, default=dict)
    valid_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class InstrumentRow(Base):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    calibration_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Instrument export parser id (rheometer CSV, colorimeter Excel ...).
    parser: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LabTaskRow(Base):
    """A work order generated from one DOE run (or created by hand)."""

    __tablename__ = "lab_tasks"
    __table_args__ = (Index("ix_lab_tasks_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doe_plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    formulation_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_metrics: Mapped[list] = mapped_column(JSON, default=list)
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # open | in_progress | pending_review | done | cancelled
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SampleRow(Base):
    __tablename__ = "samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sample_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # barcode content
    lab_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("lab_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    substrate: Mapped[str] = mapped_column(String(80), default="")
    preparation: Mapped[dict] = mapped_column(JSON, default=dict)
    prepared_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ElnLinkRow(Base):
    """Mapping between a FormuNex record and a record in an external ELN."""

    __tablename__ = "eln_links"
    __table_args__ = (
        UniqueConstraint("connector", "external_id", "local_type", name="uq_eln_link"),
        Index("ix_eln_links_local", "local_type", "local_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector: Mapped[str] = mapped_column(String(24))  # elabftw | datalab | ...
    external_id: Mapped[str] = mapped_column(String(200))
    external_url: Mapped[str] = mapped_column(String(500), default="")
    local_type: Mapped[str] = mapped_column(String(24))  # lab_task | sample | measurement | experiment
    local_id: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_state: Mapped[str] = mapped_column(String(16), default="ok")  # ok | conflict | error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MeasurementAmendmentRow(Base):
    """A change to a recorded measurement (alembic 0061): the original value is never lost.

    Amending a reviewed value sends it back to review; every amendment is also in the audit chain.
    """

    __tablename__ = "measurement_amendments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    measurement_id: Mapped[str] = mapped_column(ForeignKey("measurements.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(32))  # value | unit | method_id | instrument_id | note
    old_value: Mapped[str] = mapped_column(String(200), default="")
    new_value: Mapped[str] = mapped_column(String(200), default="")
    reason: Mapped[str] = mapped_column(Text)
    amended_by: Mapped[str] = mapped_column(String(36))
    amended_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MeasurementPickRow(Base):
    """Manual choice of which reviewed measurement represents a test point (alembic 0061).

    Repeated measurements of one metric on one experiment are all kept; the value
    used downstream follows ``lab_value_strategy`` (latest / mean) unless a person
    picked one here.
    """

    __tablename__ = "measurement_picks"
    __table_args__ = (UniqueConstraint("experiment_id", "metric", name="uq_measurement_pick"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(Integer, index=True)
    metric: Mapped[str] = mapped_column(String(80))
    measurement_id: Mapped[str] = mapped_column(ForeignKey("measurements.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text, default="")
    picked_by: Mapped[str] = mapped_column(String(36))
    picked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LabTaskAttachmentRow(Base):
    """补齐计划 2-14: 任务单附件（原始记录 / 图谱 / 现场照片等），alembic 0063。

    文件落盘 ``backend/data/attachments/lab_tasks/<task_id>/``（与实验附件同根），
    DB 只存元数据 + 相对路径；删除时尽力删文件（失败不阻断行删除，记日志）。
    """

    __tablename__ = "lab_task_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("lab_tasks.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_path: Mapped[str] = mapped_column(String(500))  # 相对于 attachments 根
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ElnSyncConflictRow(Base):
    """A record changed on both sides since the last sync (alembic 0061): queued for a person."""

    __tablename__ = "eln_sync_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    link_id: Mapped[str] = mapped_column(ForeignKey("eln_links.id", ondelete="CASCADE"), index=True)
    local_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    remote_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved
    resolution: Mapped[str] = mapped_column(String(16), default="")  # keep_local | take_remote
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# Columns 0059 adds to the existing ``measurements`` table; mirrored on
# ``MeasurementRow`` in models.py and soft-added for unmigrated legacy DBs.
MEASUREMENT_LAB_COLUMNS: dict[str, str] = {
    "sample_id": "sample_id VARCHAR(36)",
    "lab_task_id": "lab_task_id VARCHAR(36)",
    "method_id": "method_id VARCHAR(36)",
    "instrument_id": "instrument_id VARCHAR(36)",
    "operator_id": "operator_id VARCHAR(36)",
    "raw_file_key": "raw_file_key VARCHAR(300)",
    "review_status": "review_status VARCHAR(16) DEFAULT 'unreviewed' NOT NULL",
    "reviewed_by": "reviewed_by VARCHAR(36)",
    "reviewed_at": "reviewed_at TIMESTAMP",
    "external_ref": "external_ref VARCHAR(200)",
}
