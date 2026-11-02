"""Campaign workbench repository — Datalab Headless ELN (SSOT) with local main-DB fallback."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..domain.objective_contract import (
    empty_measurements_template,
    normalize_objectives,
    objectives_from_snapshot,
    row_has_required_measurements,
    validate_measurements,
)
from ..domain.schemas import (
    DOEPlan,
    ProductDomain,
    Requirement,
)
from .campaign_types import WorkbenchRow
from .datalab_client import (
    datalab_block,
    validate_blocks,
)
from .session_utils import commit_session
from .models import Campaign

logger = logging.getLogger(__name__)

_PARAMS_BLOCK = "formumind_params"
_MEASUREMENTS_BLOCK = "formumind_measurements"
_CAMPAIGN_BLOCKS = (_PARAMS_BLOCK, _MEASUREMENTS_BLOCK)


def _run_async(coro):
    """Run async store methods from sync callers without nesting event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _validate_campaign_blocks(item_data: dict[str, Any]) -> None:
    validate_blocks(item_data, _CAMPAIGN_BLOCKS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def campaign_domain(campaign: Campaign) -> ProductDomain:
    """v29 H-5: the campaign's product domain; old rows (or an unknown value) fall back to anticorrosion_coating."""
    try:
        return ProductDomain(getattr(campaign, "domain", None))
    except ValueError:
        return ProductDomain.anticorrosion_coating


def _new_item_id(campaign_id: int, run_id: int) -> str:
    suffix = uuid.uuid4().hex[:8]
    return f"formumind_c{campaign_id}_r{run_id}_{suffix}"


def _blocks_for_row(
    *,
    planned_params: dict,
    actual_params: dict,
    measurements: dict,
    status: str,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        _PARAMS_BLOCK: datalab_block(
            _PARAMS_BLOCK,
            {
                "planned_params": planned_params,
                "actual_params": actual_params,
                "status": status,
                "note": note,
                "tags": list(tags or []),
            },
        ),
        _MEASUREMENTS_BLOCK: datalab_block(_MEASUREMENTS_BLOCK, dict(measurements)),
    }


def _parse_row_from_item(
    campaign_id: int,
    row_id: int,
    item_id: str,
    item_data: dict[str, Any],
) -> WorkbenchRow:
    _validate_campaign_blocks(item_data)
    blocks = item_data.get("blocks_obj") or {}
    params_block = (blocks.get(_PARAMS_BLOCK) or {}).get("data") or {}
    meas_block = (blocks.get(_MEASUREMENTS_BLOCK) or {}).get("data") or {}
    return WorkbenchRow(
        id=row_id,
        campaign_id=campaign_id,
        item_id=item_id,
        status=str(params_block.get("status") or "Pending"),
        planned_params=dict(params_block.get("planned_params") or {}),
        actual_params=dict(params_block.get("actual_params") or {}),
        measurements=dict(meas_block),
        note=params_block.get("note"),
        tags=list(params_block.get("tags") or []),
        refcode=item_data.get("refcode"),
    )


class CampaignStoreInterface(ABC):
    """Abstract campaign + workbench row persistence (Datalab or local fallback)."""

    @abstractmethod
    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
        owner_id: str | None = None,
    ) -> Campaign: ...

    @abstractmethod
    async def get_campaign(self, campaign_id: int) -> Campaign | None: ...

    @abstractmethod
    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]: ...

    async def set_row_tags(
        self, campaign_id: int, row_id: int, tags: list[str]
    ) -> WorkbenchRow | None:
        """只更新单行 tags(默认实现返回 None；子类覆盖为字段级更新)。"""
        return None

    async def set_row_note(
        self, campaign_id: int, row_id: int, note: str | None
    ) -> WorkbenchRow | None:
        """只更新单行 note(默认实现返回 None；子类覆盖为字段级更新)。"""
        return None

    @abstractmethod
    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]: ...

    @abstractmethod
    async def get_experiments(self, campaign_id: int) -> list[WorkbenchRow]:
        """Completed rows with measurements — BayBE closed-loop input."""
        ...

    async def reconcile_sample_refs(self, campaign_id: int) -> dict:
        """Detect and prune stale ``sample_refs`` whose Datalab item is gone.

        Returns ``{removed, kept, removed_count, errors}``. Network / 5xx
        failures are reported in ``errors`` and never pruned (only a definitive
        404 is treated as deleted). Idempotent.
        """
        return {"removed": [], "kept": [], "removed_count": 0, "errors": []}

    async def probe_sample_refs(self, campaign_id: int) -> dict:
        """Read-only stale-ref probe (no pruning) for the quality report.

        Returns ``{stale, valid, errors}`` where ``stale`` are definitive 404s
        and ``errors`` are transient failures (neither is modified).
        """
        return {"stale": [], "valid": [], "errors": []}

    def list_rows_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        return _run_async(self.list_rows(campaign_id))

    def get_experiments_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        return _run_async(self.get_experiments(campaign_id))

    def get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        return _run_async(self.get_campaign(campaign_id))

    async def close(self) -> None:
        """Release external resources (no-op for the local main-DB store)."""
        return None


class _CampaignMetaMixin:
    """Shared Campaign metadata writes (main database — not experiment measurements)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._write_lock = threading.RLock()

    def _create_campaign_meta(
        self,
        plan: DOEPlan,
        *,
        name: str | None,
        strategy: str,
        req: Requirement | None,
        project_id: str | None,
        owner_id: str | None = None,
    ) -> Campaign:
        campaign_name = name or f"DOE {plan.design} ({plan.plan_id[:8] or 'local'})"
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        from ..domain.project_spec import lever_snapshot_from_plan

        lever_snapshot = lever_snapshot_from_plan(plan, req)
        primary = objectives[0].metric if objectives else None
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = Campaign(
                    name=campaign_name,
                    strategy=strategy,
                    status="IN_PROGRESS",
                    project_id=project_id,
                    owner_id=owner_id,
                    primary_metric=primary,
                    # v29 H-5: persist domain (was hardcoded on read)
                    domain=domain.value if hasattr(domain, "value") else str(domain),
                    objectives_snapshot=[o.model_dump() for o in objectives],
                    lever_snapshot=lever_snapshot,
                    sample_refs=[],
                )
                session.add(campaign)
                session.flush()
                session.refresh(campaign)
                return campaign

    def _get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        with self._session_factory() as session:
            return session.get(Campaign, campaign_id)

    def _save_sample_refs(self, campaign_id: int, refs: list[dict]) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                campaign.sample_refs = refs
                campaign.updated_at = _utcnow()

    def _append_loop_history(self, campaign_id: int, entry: dict) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                history = list(campaign.loop_history or [])
                entry = dict(entry)
                entry["round"] = len(history) + 1
                history.append(entry)
                campaign.loop_history = history
                campaign.updated_at = _utcnow()

    def append_loop_history_sync(self, campaign_id: int, entry: dict) -> None:
        self._append_loop_history(campaign_id, entry)

    def clear_loop_history_sync(self, campaign_id: int) -> bool:
        """清空 campaign 的 loop_history(收敛后手动重启)。返回 False 表示 campaign 不存在。"""
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return False
                campaign.loop_history = []
                campaign.updated_at = _utcnow()
                return True

    def update_last_loop_entry_sync(self, campaign_id: int, updates: dict) -> bool:
        """原地更新 loop_history 最后一个 entry(用于 running → 终态)。

        G-1: 保留原 entry 的 round —— 调用方传的 updates 里 "round": 0
        只是占位，不能覆盖 running entry 已分配的 round 号。
        """
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return False
                history = list(campaign.loop_history or [])
                if not history:
                    return False
                entry = dict(history[-1])
                _orig_round = entry.get("round")
                entry.update(updates)
                if _orig_round is not None:
                    entry["round"] = _orig_round
                entry.pop("running", None)
                history[-1] = entry
                campaign.loop_history = history
                campaign.updated_at = _utcnow()
                return True

    def _update_campaign_status(self, campaign_id: int, rows: list[WorkbenchRow]) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                completed = sum(1 for r in rows if r.status == "Completed")
                total = len(rows)
                campaign.status = "COMPLETED" if total > 0 and completed == total else "IN_PROGRESS"
                campaign.updated_at = _utcnow()


class LocalCampaignStore(_CampaignMetaMixin, CampaignStoreInterface):
    """Local JSON-in-Campaign fallback when Datalab is unreachable (tests / offline)."""

    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
        owner_id: str | None = None,
    ) -> Campaign:
        campaign = self._create_campaign_meta(
            plan, name=name, strategy=strategy, req=req, project_id=project_id, owner_id=owner_id
        )
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        meas_template = empty_measurements_template(objectives)
        refs: list[dict] = []
        for idx, run in enumerate(plan.runs, start=1):
            planned = dict(run.natural)
            item_id = f"local_c{campaign.id}_r{idx}"
            refs.append(
                {
                    "id": idx,
                    "item_id": item_id,
                    "status": "Pending",
                    "planned_params": planned,
                    "actual_params": dict(planned),
                    "measurements": dict(meas_template),
                }
            )
        self._save_sample_refs(campaign.id, refs)
        campaign.sample_refs = refs
        return campaign

    async def get_campaign(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)

    def _refs_to_rows(self, campaign: Campaign) -> list[WorkbenchRow]:
        return [
            WorkbenchRow(
                id=int(ref["id"]),
                campaign_id=campaign.id,
                item_id=str(ref.get("item_id") or f"local_{ref['id']}"),
                status=str(ref.get("status") or "Pending"),
                planned_params=dict(ref.get("planned_params") or {}),
                actual_params=dict(ref.get("actual_params") or {}),
                measurements=dict(ref.get("measurements") or {}),
                note=str(ref["note"]) if ref.get("note") else None,
                tags=list(ref.get("tags") or []),
                parent_sample_id=str(ref["parent_sample_id"]) if ref.get("parent_sample_id") else None,
                parent_campaign_id=int(ref["parent_campaign_id"]) if ref.get("parent_campaign_id") else None,
            )
            for ref in (campaign.sample_refs or [])
        ]

    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        return self._refs_to_rows(campaign)

    async def set_row_tags(
        self, campaign_id: int, row_id: int, tags: list[str]
    ) -> WorkbenchRow | None:
        """Field-only tags update on local sample_refs (no full-row rewrite)."""
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return None
        refs = list(campaign.sample_refs or [])
        hit = False
        for ref in refs:
            if int(ref["id"]) != row_id:
                continue
            ref["tags"] = list(tags or [])
            hit = True
            break
        if not hit:
            return None
        self._save_sample_refs(campaign_id, refs)
        campaign.sample_refs = refs
        return next((r for r in self._refs_to_rows(campaign) if r.id == row_id), None)

    async def set_row_note(
        self, campaign_id: int, row_id: int, note: str | None
    ) -> WorkbenchRow | None:
        """Field-only note update on local sample_refs (no full-row rewrite)."""
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return None
        refs = list(campaign.sample_refs or [])
        hit = False
        for ref in refs:
            if int(ref["id"]) != row_id:
                continue
            ref["note"] = note
            hit = True
            break
        if not hit:
            return None
        self._save_sample_refs(campaign_id, refs)
        campaign.sample_refs = refs
        return next((r for r in self._refs_to_rows(campaign) if r.id == row_id), None)

    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return 0, []

        objectives = objectives_from_snapshot(campaign.objectives_snapshot, campaign_domain(campaign))

        refs = list(campaign.sample_refs or [])
        ref_by_id = {int(r["id"]): r for r in refs}
        updated = 0

        for payload in rows:
            row_id = int(payload["id"])
            ref = ref_by_id.get(row_id)
            if ref is None:
                continue
            ref["actual_params"] = payload.get("actual_params") or {}
            raw_meas = payload.get("measurements") or {}
            try:
                ref["measurements"] = validate_measurements(raw_meas, objectives, strict=True)
            except ValueError:
                ref["measurements"] = validate_measurements(raw_meas, objectives)
            status = payload.get("status") or ref.get("status") or "Pending"
            if row_has_required_measurements(ref["measurements"], objectives):
                status = "Completed"
            ref["status"] = status
            # Phase 2: persist note + tags
            if "note" in payload:
                ref["note"] = payload["note"]
            if "tags" in payload:
                ref["tags"] = list(payload["tags"] or [])
            updated += 1

        self._save_sample_refs(campaign_id, refs)
        campaign.sample_refs = refs
        refreshed = self._refs_to_rows(campaign)
        self._update_campaign_status(campaign_id, refreshed)
        return updated, refreshed

    async def get_experiments(self, campaign_id: int) -> list[WorkbenchRow]:
        rows = await self.list_rows(campaign_id)
        return [r for r in rows if r.status == "Completed"]

    def list_rows_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        return self._refs_to_rows(campaign)

    def get_experiments_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        return [r for r in self.list_rows_sync(campaign_id) if r.status == "Completed"]

    def get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)


_store: CampaignStoreInterface | None = None


def get_campaign_store(settings: Settings | None = None) -> CampaignStoreInterface:
    """The campaign store is always the local main-DB ledger (Datalab removal
    batch 2: the DatalabCampaignStore and the datalab/auto switch are gone).
    ``settings`` is accepted for call-site compatibility; only the deprecated
    ``sqlite`` alias is still honoured (with a warning)."""
    global _store
    if _store is not None:
        return _store
    s = settings or get_settings()
    from .database import default_session_factory

    factory = default_session_factory()
    backend = (s.campaign_backend or "native").lower()
    if backend == "sqlite":
        # Phase 2D (2-24): "sqlite" is a deprecated alias of "native" (same store, same dialect as db_url).
        logger.warning("campaign_backend 'sqlite' is a deprecated alias of 'native'")
        backend = "native"
    # native: FormuNex's own database is the system of record (方案 v1.1 §5.3.1).
    if backend not in ("native",):
        logger.warning("Unknown campaign_backend %r; using the native store", backend)
    _store = LocalCampaignStore(factory)
    return _store


def reset_campaign_store(store: CampaignStoreInterface | None = None) -> None:
    """Test helper — inject a store or clear the singleton."""
    global _store
    _store = store
