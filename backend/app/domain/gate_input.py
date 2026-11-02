"""Pure PLM release-gate inputs (stage 4 / SR-022).

The gate rules used to read SQLAlchemy rows throughout, which meant the
rules could only be tested against a database. These dataclasses are the
gate's pure input boundary: every field is plain data (no Row, no
Session), so the pure checks in ``services/plm/release/gate.py`` can run
on a hand-built ``GateInput`` with no database at all.

Each dataclass mirrors exactly the attributes its check reads on the
corresponding row, so the checks are duck-typed: they accept a row or
the dataclass interchangeably. This module imports only the standard
library and is importable from every layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class FormulationData:
    """A formulation version's frozen snapshot (mirrors FormulationVersion).

    ``_check_total`` reads only ``snapshot``; ``version_id`` is carried
    for identification.
    """

    snapshot: dict[str, Any] | None
    version_id: str | None = None


@dataclass
class DocumentData:
    """A regulatory document's gate-relevant fields (mirrors RegulatoryDocumentRow).

    ``regulation`` is the effective regulation (the row's ``regulation``
    or the default when absent), exactly what the checks compute with
    ``getattr(d, "regulation", None) or DEFAULT_REGULATION``.
    """

    id: str
    doc_type: str
    lang: str
    version_no: int
    status: str
    batch_id: str | None
    regulation: str
    content: dict[str, Any]


@dataclass
class MeasuredValue:
    """A reviewed measurement (mirrors MeasurementRow fields the objectives check reads)."""

    id: str
    value: float
    unit: str | None


@dataclass
class WaiverData:
    """An e-signed waiver (mirrors ReleaseGateWaiverRow fields Check.to_dict reads)."""

    id: str
    reason: str | None
    waived_by: str | None
    created_at: datetime


@dataclass
class GateInput:
    """Everything the pure gate checks need, with no Row and no Session.

    ``classification_results`` is optional and deliberately NOT a gate
    input proper: it carries precomputed classification outputs
    (``inputs_hash -> result`` dicts, as ``_check_classification``
    returns) so the data-gaps check can also run purely when a caller
    already has them. When it is ``None``, ``evaluate_input`` reports
    data_gaps as not_applicable instead of inventing results.
    """

    revision_id: str
    product_code: str | None
    rev: str | None
    formulation: FormulationData | None
    items: list[dict[str, Any]]
    documents: list[DocumentData]
    document_problems: list[tuple[str, str, str]]
    acceptance: list[dict[str, Any]]
    measured: dict[str, MeasuredValue]
    picks: dict[str, str]
    winning_experiment_id: str | None
    waivers: dict[tuple[str, str, str | None], WaiverData]
    classification_results: dict[str, dict[str, Any]] | None = None
