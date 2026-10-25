"""Substance-level regulatory master data (alembic 0052 / 0053).

Two-layer model: a *substance* is a CAS-level chemical entity carrying
classifications, exposure limits and transport data; a *material* (the existing
``materials`` table) is something purchasable, often itself a mixture whose
composition is recorded in ``material_compositions``. GB 30000 mixture
classification explodes a formulation through materials down to substances.

Classifications are stored per *source* rather than overwritten, because the
same substance is routinely classified differently by the China hazardous
chemicals catalogue, a supplier SDS and PubChem. The effective value is picked
by ``services.plm.regulatory.source_priority``:
manual (locked) > supplier_sds > cn_catalog > pubchem > inferred.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _utcnow


class RegulatoryDataRelease(Base):
    """One run of the seed / import pipeline — every derived row records which
    release produced it, so a classification can be traced to its data."""

    __tablename__ = "regulatory_data_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(40), default="seed")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    input_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SubstanceRow(Base):
    __tablename__ = "substances"
    __table_args__ = (
        Index("ix_substances_name_zh", "name_zh"),
        # Case-insensitive name lookup in chemical_lookup must stay index-backed:
        # a plain index does not serve ``lower(col) = ...``, so the local-library
        # tier (alembic 0065) reads through these expression indexes.
        Index("ix_substances_name_zh_lower", text("lower(name_zh)")),
        Index("ix_substances_name_en_lower", text("lower(name_en)")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Unique when present; generic catalogue entries ("六价铬化合物") have none.
    cas_no: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)
    ec_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name_zh: Mapped[str] = mapped_column(String(300), default="")
    name_en: Mapped[str] = mapped_column(String(300), default="", index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    mf: Mapped[str | None] = mapped_column(String(120), nullable=True)
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = unknown. Must only be set from the official IECSC list — the bulk
    # common_chemicals import does not say which inventory it came from.
    iecsc_listed: Mapped[bool | None] = mapped_column(nullable=True)
    # 《危险化学品目录（2015 版）》 entry number when listed.
    hazardous_catalog_no: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 《危险化学品目录》备注栏：剧毒 / 易制毒 / 易制爆。
    highly_toxic: Mapped[bool | None] = mapped_column(nullable=True)
    key_supervised: Mapped[bool | None] = mapped_column(nullable=True)
    precursor_control: Mapped[bool | None] = mapped_column(nullable=True)
    explosive_precursor: Mapped[bool | None] = mapped_column(nullable=True)
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SubstanceClassificationRow(Base):
    """One hazard class / category for a substance, from one source."""

    __tablename__ = "substance_classifications"
    __table_args__ = (
        UniqueConstraint(
            "substance_id", "hazard_class", "source", "category",
            name="uq_substance_classification",
        ),
        Index("ix_substance_cls_substance_source", "substance_id", "source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    substance_id: Mapped[str] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), index=True
    )
    # Stable code, e.g. "acute_tox_oral", "skin_corrosion", "aquatic_chronic".
    hazard_class: Mapped[str] = mapped_column(String(48), index=True)
    # "1", "1A", "2", "A" (types), "1.1" (divisions), "unstable", "lact" (哺乳附加类别)
    category: Mapped[str] = mapped_column(String(16))
    h_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Catalogue asterisk: the listed category is a minimum, not a final call.
    is_minimum: Mapped[bool] = mapped_column(default=False)
    # Free-text qualifier (e.g. "（呼吸道刺激）", a concentration condition).
    qualifier: Mapped[str] = mapped_column(String(200), default="")
    scl: Mapped[list | None] = mapped_column(JSON, nullable=True)  # specific concentration limits
    m_factor_acute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    m_factor_chronic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ate_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ate_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # manual | supplier_sds | cn_catalog | pubchem | inferred
    source: Mapped[str] = mapped_column(String(16), index=True)
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    locked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SubstanceOELRow(Base):
    """GBZ 2.1 occupational exposure limits (mg/m³)."""

    __tablename__ = "substance_oel"
    __table_args__ = (UniqueConstraint("substance_id", "standard", name="uq_substance_oel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    substance_id: Mapped[str] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), index=True
    )
    standard: Mapped[str] = mapped_column(String(40), default="GBZ 2.1-2019")
    mac: Mapped[float | None] = mapped_column(Float, nullable=True)
    pc_twa: Mapped[float | None] = mapped_column(Float, nullable=True)
    pc_stel: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_effect: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(String(200), default="")
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SubstanceToxRecordRow(Base):
    """One measured toxicology endpoint for a substance (MSDS ToxRecord).

    ``value`` stays a string, as in MSDS: it may carry a comparator or a
    range (">2000", "3 - 3.25"). The route is explicit here — MSDS passes
    the endpoint at render time, but a stored record needs it.
    """

    __tablename__ = "substance_tox_records"
    __table_args__ = (UniqueConstraint("substance_id", "endpoint", "route", "species", "source",
                                       name="uq_substance_tox"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    substance_id: Mapped[str] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(String(16), default="LD50")  # LD50 / LC50
    route: Mapped[str] = mapped_column(String(16), default="oral")  # oral / dermal / inhalation / other
    value: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(16), default="mg/kg")
    species: Mapped[str] = mapped_column(String(64), default="大鼠")
    method: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SubstanceEcoRecordRow(Base):
    """One measured ecotoxicity endpoint (MSDS EcoTestRecord)."""

    __tablename__ = "substance_eco_records"
    __table_args__ = (UniqueConstraint("substance_id", "organism_group", "endpoint", "species", "source",
                                       name="uq_substance_eco"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    substance_id: Mapped[str] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), index=True
    )
    organism_group: Mapped[str] = mapped_column(String(16))  # fish / daphnia / algae
    endpoint: Mapped[str] = mapped_column(String(16), default="LC50")  # LC50 / EC50 / ErC50 / NOEC / LOEC
    value: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(16), default="mg/L")
    duration: Mapped[str] = mapped_column(String(32), default="")  # e.g. 96h
    species: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SubstanceEcoProfileRow(Base):
    """Per-substance fate descriptions (MSDS EcoDataDetailed, non-test part)."""

    __tablename__ = "substance_eco_profiles"
    __table_args__ = (UniqueConstraint("substance_id", name="uq_substance_eco_profile"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    substance_id: Mapped[str] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), index=True
    )
    persistence: Mapped[str] = mapped_column(String(300), default="")
    bioaccum: Mapped[str] = mapped_column(String(300), default="")
    mobility: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SubstanceTransportRow(Base):
    """GB 12268 dangerous-goods list entry linked to a substance (or generic)."""

    __tablename__ = "substance_transport"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    un_number: Mapped[str] = mapped_column(String(8), index=True)
    substance_id: Mapped[str | None] = mapped_column(
        ForeignKey("substances.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proper_shipping_name_zh: Mapped[str] = mapped_column(String(500), default="")
    proper_shipping_name_en: Mapped[str] = mapped_column(String(500), default="")
    transport_class: Mapped[str] = mapped_column(String(8), default="")
    subsidiary_risk: Mapped[str] = mapped_column(String(32), default="")
    packing_group: Mapped[str | None] = mapped_column(String(4), nullable=True)
    marine_pollutant: Mapped[bool | None] = mapped_column(nullable=True)
    special_provisions: Mapped[str] = mapped_column(String(200), default="")
    standard: Mapped[str] = mapped_column(String(40), default="GB 12268")
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class GhsStatementRow(Base):
    """H / P statement texts (Chinese + English)."""

    __tablename__ = "ghs_statements"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)  # H315 / P305+P351+P338
    kind: Mapped[str] = mapped_column(String(1), index=True)  # H | P
    text_zh: Mapped[str] = mapped_column(Text, default="")
    text_en: Mapped[str] = mapped_column(Text, default="")
    hazard_type: Mapped[str] = mapped_column(String(80), default="")
    hazard_category: Mapped[str] = mapped_column(String(120), default="")
    precaution_category: Mapped[str] = mapped_column(String(40), default="")  # P: 预防/响应/储存/处置
    usage_condition: Mapped[str] = mapped_column(Text, default="")


class SubstanceGenericEntryRow(Base):
    """Catalogue entries without a CAS number that name a *group* of
    substances ("六价铬化合物", "氰化物"). Matching is by name / element rules
    and always surfaces for human confirmation."""

    __tablename__ = "substance_generic_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    catalog_no: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name_zh: Mapped[str] = mapped_column(String(300), default="")
    name_en: Mapped[str] = mapped_column(String(300), default="")
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    hazard_text: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[list] = mapped_column(JSON, default=list)
    match_rule: Mapped[dict] = mapped_column(JSON, default=dict)
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RegulatoryReviewItem(Base):
    """Something the import pipeline could not decide on its own: an invalid
    CAS, an unparseable hazard text, a conditional classification."""

    __tablename__ = "regulatory_review_queue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # invalid_cas | unparsed_hazard | conditional | ...
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved | ignored
    substance_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    cas_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MaterialCompositionRow(Base):
    """A raw material's composition in substances (from its supplier SDS)."""

    __tablename__ = "material_compositions"
    __table_args__ = (
        UniqueConstraint("material_id", "substance_id", name="uq_material_composition"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), index=True
    )
    substance_id: Mapped[str] = mapped_column(ForeignKey("substances.id"), index=True)
    pct_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_typical: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_trade_secret: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | supplier_sds
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
