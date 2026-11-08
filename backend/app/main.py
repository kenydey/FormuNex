"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

import re
import logging
import os
from contextlib import asynccontextmanager

# 2026-09-04: app logger 无 handler 导致所有业务日志(chat failed traceback、
# 耗时分解等)静默丢失——显式挂 root handler, INFO 起步输出到 stdout
# (uvicorn 日志文件/管道一并接收)。
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .services.errors import log_handled_exception

from .api._admin_guard import admin_guard
from .api._rbac_guard import rbac_guard
from .api import auth as auth_router
from .api import chemistry as chemistry_router
from .api import doe, experiments, formulations, optimize, research, tasks
from .api import search as search_router, ingest as ingest_router, chat as chat_router, settings as settings_router
from .api import qc as qc_router
from .api import pageindex as pageindex_router
from .api import cockpit as cockpit_router
from .api import doe_designs as doe_designs_router
from .api import identity as identity_router
from .api import lab as lab_router
from .api import plm as plm_router
from .api import plm_ws as plm_ws_router
from .api import ops as ops_router
from .api import session as session_router
from .api import ip_analysis as ip_router
from .api import loop as loop_router
from .api import design as design_router
from .api import intent as intent_router
from .api import dependencies as dependencies_router
from .api import kb as kb_router
from .api import embedding_models as embedding_models_router
from .api import wiki as wiki_router
from .api import materials as materials_router
from .api import kg as kg_router
from .api import kg_neo4j as kg_neo4j_router
from .api import surechembl as surechembl_router
from .api import org as org_router
from .api import compliance as compliance_router
from .api import meta as meta_router
from .api import session_plans as session_plans_router
from .api import tech_reports as tech_reports_router
from .api import memories as memories_router
from .api import mcp_approvals as mcp_approvals_router
from .api import provenance as provenance_router
from .api import review_runs as review_runs_router
from .api import artifact_versions as artifact_versions_router
from .api import smart_collections as smart_collections_router
from .api import projects as projects_router
from .api import formulation_skills as formulation_skills_router
from .api import skills as skills_router
from .api import connectors as connectors_router
from .api import health as health_router
from .api.health import health, health_detailed, reset_health_cache  # noqa: F401 — re-exported for existing importers (SR-089)
from .config import get_settings
from .middleware.api_auth import install_api_auth
from .middleware.rate_limit import RateLimitMiddleware

logger = logging.getLogger(__name__)
settings = get_settings()

_SKIP_BOOTSTRAP_ENV = "FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP"


def _skip_lifespan_bootstrap() -> bool:
    """Return True when FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP is truthy.

    Test-speedup flag ONLY: when set ("1"/"true"/"yes", case-insensitive),
    the lifespan manager skips the heavy bootstrap steps (secrets
    reload_settings, ColBERT seed-corpus bootstrap, PubChem enrichment).
    Default (unset/falsy) behaviour is completely unchanged.

    P1-A10: a production process never honours this flag, even if it
    leaks in from a copied test environment — silently skipping the DB
    head gate would let a stale schema serve. The refusal is logged so
    the misconfiguration is visible instead of silent.
    """
    if os.environ.get(_SKIP_BOOTSTRAP_ENV, "").strip().lower() not in {"1", "true", "yes"}:
        return False
    if settings.environment.strip().lower() in {"production", "prod"}:
        logger.error(
            "FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP is set but environment=%s: "
            "refusing to skip lifespan bootstrap",
            settings.environment,
        )
        return False
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bootstrap ColBERT seed corpus and optional PubChem enrichment.

    Honours FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP (test-only fast path, see
    _skip_lifespan_bootstrap) to skip the heavy bootstrap steps while keeping
    the lightweight ELN store branch and shutdown semantics intact.
    """
    global settings  # P2-11: reassigned below by reload_settings()
    skip_bootstrap = _skip_lifespan_bootstrap()
    if skip_bootstrap:
        logger.warning(
            "lifespan: test fast path active — skipping predictor prewarm, "
            "startup DB checks, builtin-role sync, settings reload, ColBERT "
            "bootstrap, material seed, PubChem enrichment, RAG preheat"
        )
    # ------------------------------------------------------------------
    # R4: 预热 predictor 冷启动(首个 predict 实测 9-29s: thermo 数据库
    # 初始化 + rdkit import)——后台线程, 不阻塞启动; 失败静默(首个
    # 请求再付一次冷启动, 不报错)。测试环境 SKIP 隔离, 不慢化现有测试。
    # ------------------------------------------------------------------
    if not skip_bootstrap:
        import threading

        from .services.predictor import warm_predict

        threading.Thread(
            target=warm_predict, name="predictor-prewarm", daemon=True
        ).start()
        # 2026-09-05: celery producer 首建在 uvicorn 进程内会卡请求线程
        # (submit 全挂)——启动期后台预热, 请求期不再付首次初始化。
        from .worker.tasks import warm_celery_producer

        threading.Thread(
            target=warm_celery_producer, name="celery-producer-warmup", daemon=True
        ).start()
    # ------------------------------------------------------------------
    # Fail-fast: ensure the API token is resolvable at startup so a missing
    # FORMUNEX_API_TOKEN surfaces immediately in production instead of on
    # the first authenticated request.
    # ------------------------------------------------------------------
    if settings.api_auth_enabled:
        from .middleware.api_auth import resolve_api_token

        resolve_api_token(settings)
    # ------------------------------------------------------------------
    # Wave 4: fail-fast on truly required startup configuration (bad db_url
    # scheme, unwritable sqlite data dir).
    # Cheap and deterministic — runs unconditionally, never blocks on network.
    # ------------------------------------------------------------------
    from .startup_checks import run_startup_checks

    for warning in run_startup_checks(settings):
        logger.warning("startup check: %s", warning)
    # Stage 1 / SR-036: database head + schema gate. Runs only on real
    # starts, never on the test fast-path (conftest sets
    # FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP=1, and unit tests call
    # run_startup_checks directly instead). A refusal propagates out of
    # lifespan, so uvicorn exits instead of serving a stale schema.
    if not skip_bootstrap:
        from .startup_checks import run_startup_db_checks

        for warning in run_startup_db_checks(settings):
            logger.warning("startup check: %s", warning)
    # Stage 5 / SR-080: built-in roles are code-owned — converge the
    # database to the live definition on every real boot (insert
    # missing roles, reset edited permission sets). The mechanism
    # existed and was unit-tested but no production path ever called
    # it, so a drifted role set survived restarts. Failure here only
    # warns: roles stay as they are (today's behaviour) and the
    # failure is visible in the log instead of silent.
    if not skip_bootstrap:
        try:
            from .db.database import default_session_factory
            from .services.identity.rbac import sync_builtin_roles

            with default_session_factory()() as session:
                changed = sync_builtin_roles(session)
                session.commit()
            if changed:
                logger.warning("startup: built-in roles converged: %s", changed)
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: sync_builtin_roles failed")
    # P1 #25: production + SQLite is a foot-gun under multi-worker write load.
    try:
        env = (settings.environment or "").strip().lower()
        db = (settings.db_url or "").strip().lower()
        if env in ("production", "prod") and db.startswith("sqlite"):
            logger.warning(
                "FORMUNEX_ENVIRONMENT=%s with SQLite db_url=%s — "
                "set FORMUNEX_DB_URL=postgresql://... for multi-process production "
                "(see docs/USER_GUIDE.md / deploy notes).",
                settings.environment,
                settings.db_url,
            )
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan: sqlite production warning failed")
    # Stage 0 / SR-013: an explicit auth opt-out must be loud. The code
    # default is now fail-closed (on); only FORMUNEX_API_AUTH_ENABLED=false
    # reaches here, and that must never be a production configuration.
    try:
        if not settings.api_auth_enabled:
            logger.warning(
                "SECURITY: api_auth_enabled=false — the API accepts unauthenticated requests. "
                "Local development only; production must set FORMUNEX_API_AUTH_ENABLED=true "
                "and FORMUNEX_ENVIRONMENT=production (see docs/deployment.md)."
            )
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan: auth-disabled warning failed")
    # ------------------------------------------------------------------
    # Recover stalled outbox rows (best-effort, must not block startup).
    # Runs in a daemon thread; under celery_eager re-dispatch is skipped so
    # leftover PENDING recommend/deep/inverse jobs cannot keep :8000 closed.
    # ------------------------------------------------------------------
    try:
        from .db.dispatcher import schedule_periodic_recover_stalled, schedule_recover_stalled

        schedule_recover_stalled()
        # Stage 1 / SR-048: heal mid-run stalls without a restart. The
        # thread sleeps its interval before the first pass and is a
        # daemon, so it cannot delay startup or shutdown.
        if settings.outbox_recover_interval_s > 0:
            schedule_periodic_recover_stalled(settings.outbox_recover_interval_s)
    except Exception:
        logger.exception("lifespan: outbox stall recovery failed (non-fatal)")
    if not skip_bootstrap:
        try:
            from .services.secrets_store import reload_settings

            # P2-11: reload_settings() rebuilds and returns a NEW Settings
            # object; discarding it left this module's global (read by the
            # material-seed / PubChem branches below and by the enrich
            # closure) on import-time values while get_settings() elsewhere
            # returned the UI-persisted ones — one process, two configs.
            # Deliberately NOT retro-updated: middleware installed at app
            # build (CORS, rate limit) and the branches above this point
            # (API-token resolve, startup checks, role sync) captured
            # import-time values; those are env-driven, not UI flags.
            settings = reload_settings()
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: reload_settings failed")
        try:
            from .services import colbert_store

            backend = colbert_store.active_backend()
            if backend == "colbert" or backend == "pylate":
                colbert_store.bootstrap_seed_corpus()
            else:
                logger.info("lifespan: skipping ColBERT bootstrap (backend=%s)", backend)
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: ColBERT bootstrap failed")
        if settings.material_store_enabled:
            try:
                from .db.material_store import get_material_store
                from .domain.knowledge import RAW_MATERIALS, _SEED_MATERIALS

                get_material_store().seed_missing(_SEED_MATERIALS)
                RAW_MATERIALS.refresh()
            except Exception as exc:
                log_handled_exception(logger, exc, "lifespan: material seed failed")
        if settings.enrich_compounds:
            # 2026-09-05: enrich 原同步阻塞启动 —— 材料库未 rich 时逐材料
            # PubChem + primp 网页搜索兜底, 启动可拖 10+ 分钟(前端 502 根因)。
            # 改后台线程: 启动不等网络; 完成后 persist, 下次启动跳过。
            def _bg_enrich() -> None:
                try:  # pragma: no cover - opt-in network path
                    from .domain.knowledge import RAW_MATERIALS
                    from .services.compounds import enrich_materials

                    enriched = enrich_materials(RAW_MATERIALS)
                    # enrich_materials mutates the spec dicts in place; persist so
                    # the backfilled SMILES survive a restart instead of costing a
                    # PubChem round-trip on every boot.
                    if enriched and settings.material_store_enabled:
                        RAW_MATERIALS.persist_all()
                except Exception as exc:
                    log_handled_exception(logger, exc, "lifespan: PubChem enrichment failed")

            import threading

            threading.Thread(target=_bg_enrich, name="pubchem-enrich", daemon=True).start()
    # Datalab removal batch 2: the stores are always native; warm the
    # singletons so first requests do not pay session-factory setup.
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store

        get_campaign_store(settings)
        get_experiment_store(settings)
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan: ELN store initialization failed", level=logging.ERROR)
    # Initialize session memory service (startup phase)
    try:
        await session_router.init_session_memory_on_startup()
    except Exception as e:
        logger.error(f"Failed to initialize session memory service: {e}")
    # RAG 冷启动预热（非阻塞，2s 后后台触发）
    if not skip_bootstrap:
        try:
            import asyncio as _asyncio

            from .services.rag_preheat import preheat as _rag_prewarm

            loop = _asyncio.get_running_loop()

            def _schedule():
                _rag_prewarm(background=True)

            loop.call_later(2.0, _schedule)
            logger.info("lifespan: scheduled rag prewarm in 2s")
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: schedule rag prewarm failed")
    yield
    try:
        # Close session memory service (shutdown phase)
        await session_router.close_session_memory_on_shutdown()
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan shutdown: session memory close failed")
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store

        if not skip_bootstrap:
            from .services.secrets_store import reload_settings

            reload_settings()
        await get_campaign_store().close()
        get_experiment_store().close()
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan shutdown: store close failed")
    try:
        # B-12：关闭 MCP 子进程（client 缓存 + reader 线程），异常安全不阻断 shutdown
        from .services.mcp_client import shutdown_all_sessions

        shutdown_all_sessions()
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan shutdown: MCP sessions close failed")


app = FastAPI(
    title="FormuNex",
    description="AI-assisted formulation R&D platform for metal surface treatment "
    "(anti-corrosion coatings, degreasers, surface treatment agents).",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(admin_guard), Depends(rbac_guard)],
)

# The last middleware added is the outermost. CORS has to wrap auth and rate limiting: their 401 / 429 answers are built
# inside the stack, and without the CORS headers a browser reports them as an opaque network error instead of
# "invalid token" / "rate limited".
app.add_middleware(RateLimitMiddleware)
install_api_auth(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    # The desktop UI (tauri://localhost) calls the API cross-origin and reads download names and render
    # diagnostics from response headers (Content-Disposition, X-Label-Overflow, X-Font-Embedded, …).
    expose_headers=["*"],
)
# Phase 2D (2-25): HTTP audit is outermost so it also sees requests rejected by auth (401) / rate limiting
# (429). It skips OPTIONS itself, so CORS preflight passes through untouched.
from .middleware.audit import AuditMiddleware

app.add_middleware(AuditMiddleware)

app.include_router(research.router)
app.include_router(doe.router)
app.include_router(optimize.router)
app.include_router(tasks.router)
app.include_router(formulations.router)
app.include_router(experiments.router)
app.include_router(search_router.router, prefix="/api")
app.include_router(ingest_router.router, prefix="/api")
app.include_router(chat_router.router, prefix="/api")
app.include_router(kb_router.router, prefix="/api")
app.include_router(pageindex_router.router, prefix="/api")
app.include_router(plm_router.router, prefix="/api")
app.include_router(plm_ws_router.ws_router)  # /ws/formulations/{id}/compliance (no /api prefix)
app.include_router(identity_router.router, prefix="/api")
app.include_router(doe_designs_router.router, prefix="/api")
app.include_router(cockpit_router.router, prefix="/api")
app.include_router(lab_router.router, prefix="/api")
app.include_router(embedding_models_router.router, prefix="/api")
app.include_router(wiki_router.router, prefix="/api")
app.include_router(session_router.router)
app.include_router(session_plans_router.router)
app.include_router(tech_reports_router.router)
app.include_router(memories_router.router)
app.include_router(mcp_approvals_router.router)
app.include_router(provenance_router.router)
app.include_router(review_runs_router.router)
app.include_router(artifact_versions_router.router)
app.include_router(smart_collections_router.router)
app.include_router(materials_router.router, prefix="/api")
app.include_router(kg_router.router, prefix="/api")
app.include_router(kg_neo4j_router.router, prefix="/api")
app.include_router(surechembl_router.router, prefix="/api")
app.include_router(org_router.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(qc_router.router, prefix="/api")
app.include_router(ops_router.router)
app.include_router(ip_router.router)
app.include_router(loop_router.router)
app.include_router(design_router.router)
app.include_router(intent_router.router)
app.include_router(dependencies_router.router)
app.include_router(chemistry_router.router)
app.include_router(projects_router.router)
app.include_router(formulation_skills_router.router)
app.include_router(skills_router.router)
app.include_router(connectors_router.router)
app.include_router(compliance_router.router)
app.include_router(meta_router.router)
app.include_router(auth_router.router)
app.include_router(health_router.router)  # /health, /health/detailed (no /api prefix, as before)




from .strict_json import install as _install_strict_json  # noqa: E402

_install_strict_json()


_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _is_out_of_range_int(value: object) -> bool:
    """True for an integer (or digit string) outside the int64 range every
    id column uses. A digit string longer than 19 digits cannot be in
    range, so it is decided by length alone (and never converted — Python
    caps ``int()`` digit counts, and a hostile 5000-digit query param must
    not become a second exception inside the handler)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value < _INT64_MIN or value > _INT64_MAX
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("+", "-"):
            s = s[1:]
        if s.isdigit():
            return len(s) > 19 or not (_INT64_MIN <= int(s) <= _INT64_MAX)
    return False


def _walk_out_of_range_ints(value: object) -> bool:
    if _is_out_of_range_int(value):
        return True
    if isinstance(value, dict):
        return any(_walk_out_of_range_ints(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_walk_out_of_range_ints(v) for v in value)
    return False


async def _request_carries_out_of_range_int(request: Request | None) -> bool:
    """Stage 5 / SR-096: does this request itself carry an out-of-range id?

    The handler cannot see the value that overflowed, so it decides by
    range instead of by exception text: path params, query params and a
    JSON body are scanned for integers outside int64. A request carrying
    one cannot name an existing row, so its OverflowError is the id
    overflow. Anything else re-raises as a defect.

    Dialect note: SQLite raises ``OverflowError`` on bind; PostgreSQL
    raises ``sqlalchemy.exc.DataError`` (NumericValueOutOfRange) for the
    same input instead — that class has no handler here because psycopg
    is unavailable in this environment to verify it against, and mapping
    it blind would be a behaviour claim without evidence.
    """
    if request is None:
        return False
    try:
        for v in (request.path_params or {}).values():
            if _is_out_of_range_int(v):
                return True
        for v in request.query_params.values():
            if _is_out_of_range_int(v):
                return True
    except Exception:
        return False
    try:
        body = await request.body()
    except Exception:
        return False
    if not body:
        return False
    try:
        import json as _json

        return _walk_out_of_range_ints(_json.loads(body))
    except Exception:
        return False


@app.exception_handler(OverflowError)
async def integer_out_of_range_handler(request: Request, exc: OverflowError) -> JSONResponse:
    """An id too large for the database cannot belong to any row: answer 404, not 500.

    Path / query / body ids are declared ``int`` — unbounded in Python — and every one of them reaches the database as
    a parameter, which overflows above 2**63. The fuzz found 20 endpoints answering
    ``GET /api/experiments/999999999999999999999999999999/attachments`` with a 500.
    Any other ``OverflowError`` is still a defect and still surfaces as one.
    """
    if await _request_carries_out_of_range_int(request):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    raise exc


def _json_safe_errors(errors: list) -> list:
    """Pydantic puts the exception a validator raised into ``ctx["error"]``; ``JSONResponse`` cannot serialise it, so a
    422 became a 500 (``TypeError: Object of type ValueError is not JSON serializable``) for every field whose
    validator raises ``ValueError`` - an invalid objective ``direction`` was the fuzzer's find: eight endpoints answered 500.
    The exception is sent as its message, the way FastAPI's own handler would have encoded it."""
    from fastapi.encoders import jsonable_encoder

    safe = []
    for error in errors:
        error = dict(error)
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            error["ctx"] = {k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()}
        safe.append(error)
    return jsonable_encoder(safe)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log 422 validation errors so intermittent failures are traceable.

    The default handler silently returns 422 without logging *which* field failed,
    which made the project-autosave 422s impossible to diagnose. Stage 5 /
    SR-097 extends the log to every route at debug level (debug keeps the
    noisy paths out of production logs; the summary carries path + field
    path + type only, never body values, so a rejected value cannot leak
    through the log). The response body is unchanged.
    """
    errs = exc.errors()
    detail = [
        {
            "loc": ".".join(str(x) for x in e.get("loc", [])),
            "type": e.get("type"),
        }
        for e in errs
    ]
    logger.debug("%s %s validation failed (%d errors): %s", request.method, request.url.path, len(errs), detail)
    return JSONResponse(status_code=422, content={"detail": _json_safe_errors(exc.errors())})


class _RedactQueryToken(logging.Filter):
    """The task stream takes a one-time ticket as a query parameter (EventSource cannot set headers); uvicorn's access log would
    write it, in clear, next to every progress connection. Replace the value before the record is formatted.

    Stage 5 / SR-095: the filter also sits on the root logger, so every
    stdlib logger (including the dispatch kernel, converted off loguru
    this batch) gets the same redaction — one surface for the stream
    credential in the URL (Stage 5 / SR-081: ticket, previously the
    bearer token) instead of one filter per logger. The substitution is idempotent
    (``***`` re-matches to itself), so the uvicorn.access copy and the
    root copy never double-redact. Both the message and %-args are
    scrubbed: f-string logging puts the token in the message itself."""

    _TOKEN = re.compile(r"([?&]token=)[^&\s\"]+")
    _TICKET = re.compile(r"([?&]ticket=)[^&\s\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._TOKEN.sub(r"\1***", record.msg)
            record.msg = self._TICKET.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._TOKEN.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
            record.args = tuple(self._TICKET.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: (self._TOKEN.sub(r"\1***", v) if isinstance(v, str) else v) for k, v in record.args.items()}
            record.args = {k: (self._TICKET.sub(r"\1***", v) if isinstance(v, str) else v) for k, v in record.args.items()}
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactQueryToken())
logging.getLogger().addFilter(_RedactQueryToken())


