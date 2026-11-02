"""Health probes and ledger-mode reporting (stage 5 / SR-089).

Moved out of ``app.main`` whole: the /health cache window, the live
/health/detailed probes, and the ledger-mode / worker / outbox helpers
they share. ``app.main`` keeps only assembly + lifespan orchestration
and re-exports :func:`health`, :func:`health_detailed` and
:func:`reset_health_cache` for existing importers.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter

from ..config import get_settings
from ..db.datalab_client import DATALAB_START_HINT, check_datalab_reachable
from ..services.errors import optional_import

router = APIRouter(tags=["meta"])


# /health is public and does real work (a Datalab HTTP probe, a database query, a broker connect, parser discovery): with
# nothing in front of it, one unreachable dependency made every call take ~12 s and a flood of calls pinned the thread pool.
# Probes are shared for _HEALTH_TTL_S, and while one is running everybody else gets the previous answer.
_HEALTH_TTL_S = 10.0
_health_cache: dict = {"at": 0.0, "at_iso": None, "payload": None}
_health_lock = threading.Lock()


def reset_health_cache() -> None:
    """Forget the cached probe (tests change the environment between calls)."""
    with _health_lock:
        _health_cache.update(at=0.0, at_iso=None, payload=None)


def _with_freshness(payload: dict, *, cached: bool, at_iso: str | None = None) -> dict:
    """Stage 5 / SR-094: stamp a /health answer with when it was probed.

    ``as_of``/``cached_at`` carry the same probe wall time (the plan
    names it both ways; clients may read either), and ``cached`` says
    whether this answer came from the 10 s cache. Without the stamp,
    /health (cached) and /health/detailed (live) disagree inside the
    window with no way to tell which answer is newer.
    """
    stamp = at_iso if at_iso is not None else _health_cache["at_iso"]
    return {**payload, "as_of": stamp, "cached_at": stamp, "cached": cached}


@router.get("/health")
def health() -> dict:
    """Minimal liveness probe — no sensitive infrastructure details leaked.

    Only exposes status + coarse booleans needed for monitoring; URLs,
    error messages, and installed-extras inventory live in /health/detailed
    (which requires auth). The answer carries ``as_of``/``cached_at``
    (when the payload was probed) and ``cached`` (whether it came from
    the 10 s cache) — see :func:`_with_freshness`.
    """
    now = time.monotonic()
    cached = _health_cache["payload"]
    if cached is not None and now - _health_cache["at"] < _HEALTH_TTL_S:
        return _with_freshness(cached, cached=True)
    if not _health_lock.acquire(blocking=cached is None):
        # Another request is probing right now: answer with the last
        # result instead of piling on. (blocking=False here implies
        # cached is not None, so the freshness stamp below is present.)
        return _with_freshness(cached, cached=True)
    try:
        payload = _probe_health()
        at_iso = datetime.now(timezone.utc).isoformat()
        _health_cache.update(at=time.monotonic(), at_iso=at_iso, payload=payload)
        return _with_freshness(payload, cached=False, at_iso=at_iso)
    finally:
        _health_lock.release()


def _ledger_mode(cfg, datalab_ok: bool, datalab_required: bool) -> str:
    """Where experiment rows live, for the UI badge: ``native`` (FormuNex database, the default since M7 — Datalab is
    simply not used), ``eln`` (Datalab reachable), ``eln_required_down`` (Datalab configured but down) or ``local``
    (``auto`` backends that fell back to the local ledger because Datalab is unreachable: soft-degrade)."""
    if datalab_ok:
        return "eln"
    if datalab_required:
        return "eln_required_down"
    if cfg.campaign_backend.lower() == "native" and cfg.experiment_backend.lower() == "native":
        return "native"
    return "local"


def _worker_and_outbox_status(cfg) -> tuple[dict, dict, bool]:
    """Stage 1 / SR-029-SR-068: worker liveness + outbox backlog.

    Shared by /health and /health/detailed so both use the same
    degraded formula. Worker ping is a short (2 s) Celery inspect and
    never raises; in eager mode there is no worker to probe. The
    backlog counts unfinished rows and the age of the oldest PENDING
    row — counts only, never payloads. Returns (worker, outbox,
    degraded): degraded when a required worker is dead or the backlog
    passes ``outbox_backlog_warn_count``.
    """
    worker: dict
    if cfg.celery_eager:
        worker = {"required": False, "alive": None}
        worker_degraded = False
    else:
        try:
            from ..worker.celery_app import celery_app

            ping = celery_app.control.inspect(timeout=2.0).ping()
            alive = bool(ping)
        except Exception:
            alive = False
        worker = {"required": True, "alive": alive}
        worker_degraded = not alive
    outbox: dict = {"pending": 0, "claimed": 0, "oldest_pending_minutes": None}
    backlog_degraded = False
    try:
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from ..db.database import default_session_factory
        from ..db.models import TaskOutbox

        with default_session_factory()() as session:
            counts = {
                str(st): int(n)
                for st, n in session.execute(
                    select(TaskOutbox.status, func.count())
                    .where(TaskOutbox.status.in_(["PENDING", "CLAIMED"]))
                    .group_by(TaskOutbox.status)
                ).all()
            }
            outbox["pending"] = counts.get("PENDING", 0)
            outbox["claimed"] = counts.get("CLAIMED", 0)
            oldest = session.execute(
                select(func.min(TaskOutbox.updated_at)).where(
                    TaskOutbox.status == "PENDING"
                )
            ).scalar()
            if oldest is not None:
                outbox["oldest_pending_minutes"] = round(
                    (datetime.now(timezone.utc).replace(tzinfo=None) - oldest).total_seconds() / 60.0, 1
                )
        unfinished = outbox["pending"] + outbox["claimed"]
        outbox["backlog_threshold"] = cfg.outbox_backlog_warn_count
        backlog_degraded = unfinished > cfg.outbox_backlog_warn_count
    except Exception:
        # A diagnostic must not become the failure: an unreadable
        # outbox leaves the counts at zero and does not degrade health
        # on its own (the database probe above already covers DB-down).
        pass
    return worker, outbox, (worker_degraded or backlog_degraded)


def _probe_health() -> dict:
    cfg = get_settings()

    datalab_ok, datalab_reason = check_datalab_reachable(cfg.datalab_api_url)
    datalab_required = (
        cfg.campaign_backend.lower() == "datalab"
        or cfg.experiment_backend.lower() == "datalab"
        or cfg.datalab_required
    )
    # Soft-degrade ledger mode for UI badge (Top-5″ #2).
    ledger_mode = _ledger_mode(cfg, datalab_ok, datalab_required)

    db_ok = True
    db_scheme = "postgresql" if cfg.db_url.startswith("postgresql") else "sqlite"
    try:
        from sqlalchemy import text

        from ..db.database import default_session_factory

        with default_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Every async feature — research, optimize, inverse design, search — is
    # submitted through Celery, so an unreachable broker breaks all of them
    # while the process keeps answering requests. Reporting only the database
    # let that outage look healthy from the outside.
    from ..services.dispatch import broker_reachable

    broker_ok = broker_reachable()

    # Which formats can actually be parsed. The image ships no optional
    # parsers, so a deployment can run for weeks accepting uploads and
    # indexing nothing at all — the same class of invisible outage as an
    # unreachable broker, and just as worth reporting here.
    from ..services.parsing import format_availability

    try:
        formats = format_availability()
    except Exception:
        formats = {}
    pdf_ok = bool(formats.get("pdf"))

    worker_status, outbox_status, ops_degraded = _worker_and_outbox_status(cfg)
    overall = "ok"
    if not db_ok or not broker_ok or not pdf_ok or (datalab_required and not datalab_ok) or ops_degraded:
        overall = "degraded"

    datalab_payload: dict = {
        "required": datalab_required,
        "reachable": datalab_ok,
        "campaign_backend": cfg.campaign_backend,
        "experiment_backend": cfg.experiment_backend,
        "ledger_mode": ledger_mode,
    }
    if datalab_required and not datalab_ok:
        hint = DATALAB_START_HINT
        if datalab_reason:
            hint = f"{hint} 原因：{datalab_reason}"
        datalab_payload["hint"] = hint
    elif ledger_mode == "local":
        datalab_payload["hint"] = (
            "Datalab 不可达；已启用本地 sqlite 台账（soft-degrade）。"
            "需要 ELN 时启动 compose 并设 DATALAB_REQUIRED=true。"
        )

    return {
        "status": overall,
        "database": {"ok": db_ok, "scheme": db_scheme},
        # "required" is False in eager mode, where tasks run in-process.
        "task_broker": {"required": not cfg.celery_eager, "reachable": broker_ok},
        "worker": worker_status,
        "outbox": outbox_status,
        "parsers": formats,
        "datalab": datalab_payload,
    }


def _mask_db_url(db_url: str) -> str:
    """Return scheme://host (password stripped) — safe to expose."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(db_url)
        host = parsed.hostname or ""
        if not host:
            return parsed.scheme or "db"
        return f"{parsed.scheme}://{host}"
    except Exception:
        return "db"


def _effective_provider() -> str:
    from ..services.runtime_secrets import effective_setting

    return str(effective_setting(get_settings(), "llm_provider") or "")


def _vision_health() -> dict:
    """Which model would read a figure, and is it configured at all.

    ``vision_available()`` was unreachable over HTTP, so when a figure came back
    as a degraded placeholder there was no way to ask the server why. Reports
    *configured*, never *capable* — see that function's own docstring.
    """
    try:
        from ..services.llm_roles import VISION, resolve_role
        from ..services.vision_extract import vision_available

        cfg = resolve_role(VISION)
        configured, hint = vision_available()
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "inherits": cfg.inherited,
            "configured": configured,
            "hint": hint,
        }
    except Exception as exc:  # a diagnostic endpoint must not become the failure
        return {"configured": False, "hint": f"探测失败：{str(exc)[:120]}"}


@router.get("/health/detailed")
def health_detailed() -> dict:
    """Detailed infra snapshot — behind auth (not in public paths).

    Unlike /health, this endpoint probes live on every call (no 10 s
    cache): inside /health's cache window the two endpoints can
    legitimately disagree, and /health's ``as_of``/``cached_at`` stamp
    says which answer is newer (SR-094).
    """
    cfg = get_settings()

    def _ok(pkg: str) -> bool:
        return optional_import(pkg)

    # Test-only fast path: probing optional extras imports heavy packages
    # (sentence_transformers/torch, paperqa, chemcrow ≈ 20+ s). Under the
    # FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP flag, report them as unprobed instead.
    from ..main import _skip_lifespan_bootstrap

    skip_probe = _skip_lifespan_bootstrap()

    llm_key = cfg.get_active_api_key()
    datalab_ok, datalab_reason = check_datalab_reachable(cfg.datalab_api_url)
    datalab_required = (
        cfg.campaign_backend.lower() == "datalab"
        or cfg.experiment_backend.lower() == "datalab"
        or cfg.datalab_required
    )
    ledger_mode = _ledger_mode(cfg, datalab_ok, datalab_required)

    db_ok = True
    db_scheme = "postgresql" if cfg.db_url.startswith("postgresql") else "sqlite"
    db_error: str | None = None
    try:
        from sqlalchemy import text

        from ..db.database import default_session_factory

        with default_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    # W1-10 ④: same degraded formula as /health. Detailed used to ignore the
    # broker and the parsers entirely, so a broker outage or a missing PDF
    # parser still reported ok here while /health said degraded.
    from ..services.dispatch import broker_reachable

    broker_ok = broker_reachable()

    from ..services.parsing import format_availability

    try:
        formats = format_availability()
    except Exception:
        formats = {}
    pdf_ok = bool(formats.get("pdf"))

    worker_status, outbox_status, ops_degraded = _worker_and_outbox_status(cfg)
    overall = "ok"
    if not db_ok or not broker_ok or not pdf_ok or (datalab_required and not datalab_ok) or ops_degraded:
        overall = "degraded"

    return {
        "status": overall,
        "app": cfg.app_name,
        "environment": cfg.environment,
        # Through the overlay, not off Settings: a provider switched in the UI
        # lives there, so reading the raw field reported the stale compiled
        # default and made this endpoint useless for the one question people ask
        # it — "which model am I actually talking to".
        "llm": _effective_provider() if llm_key else "offline-fallback",
        "llm_key_set": bool(llm_key),
        "vision": _vision_health(),
        "api_auth_enabled": cfg.api_auth_enabled,
        "celery_eager": cfg.celery_eager,
        "agent_bus": cfg.agent_bus_enabled,
        "database": {
            "ok": db_ok,
            "scheme": db_scheme,
            "url": _mask_db_url(cfg.db_url),
            "error": db_error,
        },
        "task_broker": {"required": not cfg.celery_eager, "reachable": broker_ok},
        "worker": worker_status,
        "outbox": outbox_status,
        "parsers": formats,
        "datalab": {
            "required": datalab_required,
            "reachable": datalab_ok,
            "url": cfg.datalab_api_url,
            "message": datalab_reason,
            "campaign_backend": cfg.campaign_backend,
            "experiment_backend": cfg.experiment_backend,
            "ledger_mode": ledger_mode,
        },
        "installed_extras": (
            {pkg: None for pkg in ("chemcrow", "paperqa", "patent_client", "sentence_transformers", "rdkit", "psycopg2")}
            if skip_probe
            else {
                # chemcrow removed 2026-09 (de-ChemCrow): nothing to probe, the key is kept (always false) so that
                # older clients reading it do not break. Deprecated - new clients should not look at it.
                "chemcrow": False,
                "paperqa": _ok("paperqa"),
                # patent-client removed 2026-10 (EPO OPS is called over httpx); key kept false like chemcrow's.
                "patent_client": False,
                "sentence_transformers": _ok("sentence_transformers"),
                "rdkit": _ok("rdkit"),
                "psycopg2": _ok("psycopg2"),
            }
        ),
    }
