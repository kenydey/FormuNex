"""Server-side permission check for core R&D writes (开发方案 §4.4) — Phase 1D (1-15).

Before Phase 1D this guard covered 13 write operations; the rest of the API
had no per-user RBAC at all. It now covers every write route plus the
sensitive reads, via four layers (exactly one per route — see
``scripts/audit_rbac.py``):

1. Guard (``_RULES``): this module. A personal access token needs the
   route's permission; a missing permission is a 403.
2. Admin guard (``_admin_guard``): settings / dependencies / embedding
   models / skills / connectors — ``admin.llm`` / ``admin.connectors``.
3. Endpoint-enforced (``ENDPOINT_ENFORCED``): the endpoint itself checks —
   used where the permission needs per-endpoint or OR logic the guard table
   cannot express (lab amend, signoff approve, label-templates), or where the
   check is ownership rather than a permission (task cancel).
4. Policy-exempt (``POLICY_EXEMPT``): routes that deliberately need no
   permission — auth flows (they establish the session), the conversational
   interface (chat / session-plans / session save), and per-session safety
   decisions (MCP approve / decide). Documented here so the audit can verify
   the exemption is intentional, not a gap.

Semantics (mirroring ``admin_guard``):
- Reads (GET / HEAD / OPTIONS) are never blocked here, except the sensitive
  reads in ``_SENSITIVE_READS`` (user / role lists).
- A service API key (or the API with auth disabled) is an operator credential,
  not a person, and keeps working as before.
- Project scoping: ``/api/projects/{project_id}`` writes check the permission
  in that project (global roles pass; a project-scoped role only passes for
  its own project). Other routes have no project in the path, so they check
  the global permission.

``formulation.read_exact`` is NOT enforced here — it is a read-side redaction
(see ``api/formulations.py``), not a write gate.
"""
from __future__ import annotations

import re

from starlette.requests import HTTPConnection

from ._principal import _bearer, current_user

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}

# (path pattern, permission). Order matters: specific rules before general ones.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # Projects — project-scoped (id is the 3rd path segment).
    (re.compile(r"^/api/projects(/|$)"), "project.write"),
    # Artifacts — project-scoped work products (lineages carry project_id).
    (re.compile(r"^/api/artifacts(/|$)"), "project.write"),
    # Formulations — compute endpoints need read; everything else is a write.
    (re.compile(r"^/api/formulations/(validate|recommend)$"), "formulation.read"),
    (re.compile(r"^/api/formulations(/|$)"), "formulation.write"),
    # Experiments — workbench (lab execution) vs model training (optimize).
    (re.compile(r"^/api/experiments/workbench(/|$)"), "lab.execute"),
    (re.compile(r"^/api/(experiments|train|models)(/|$)"), "optimize.run"),
    # DOE / optimize / loop.
    (re.compile(r"^/api/doe(/|$)"), "doe.write"),
    (re.compile(r"^/api/optimize(/|$)"), "optimize.run"),
    (re.compile(r"^/api/loop(/|$)"), "optimize.run"),
    # Inverse design submits an optimization job (outbox row is incidental).
    (re.compile(r"^/api/design(/|$)"), "optimize.run"),
    # Cockpit — pure compute, except winning which freezes a formulation.
    (re.compile(r"^/api/cockpit/winning$"), "formulation.freeze"),
    (re.compile(r"^/api/cockpit/(conformal|pareto)"), "formulation.read"),
    # Ingest / KB — read-only compute endpoints need read; the rest is a write.
    (re.compile(r"^/api/ingest(/|$)"), "knowledge.write"),
    (re.compile(r"^/api/kb/(hybrid-search|query-test|golden-eval/run|pageindex/search|sources/resolve|chunk-quality-sample)$"),
     "knowledge.read"),
    (re.compile(r"^/api/kb(/|$)"), "knowledge.write"),
    # Knowledge graph — similarity / retrieve are reads; rebuild / link write.
    (re.compile(r"^/api/kg/(formulations/similar|retrieve)$"), "knowledge.read"),
    (re.compile(r"^/api/kg(/|$)"), "knowledge.write"),
    # Materials — substitutes is analysis (read); the rest mutates the catalog.
    (re.compile(r"^/api/materials/substitutes$"), "knowledge.read"),
    (re.compile(r"^/api/materials(/|$)"), "knowledge.write"),
    # Chemistry tools — pure compute on the caller's input, nothing persisted.
    (re.compile(r"^/api/chemical/(enrich-materials|structure)$"), "chemical.read"),
    # QC.
    (re.compile(r"^/api/qc(/|$)"), "qc.write"),
    # Collections (smart collections) are curated knowledge lists.
    (re.compile(r"^/api/collections(/|$)"), "knowledge.write"),
    # Memories are the user's second-brain notes.
    (re.compile(r"^/api/memories(/|$)"), "knowledge.write"),
    # Intent parse / IP analysis / research / review rerun / search / exports:
    # pure compute (job submission or a generated view), nothing persisted.
    (re.compile(r"^/api/intent/parse$"), "formulation.read"),
    (re.compile(r"^/api/ip/analyze$"), "knowledge.read"),
    (re.compile(r"^/api/research(/|$)"), "knowledge.read"),
    (re.compile(r"^/api/reviews(/|$)"), "knowledge.read"),
    (re.compile(r"^/api/search(/|$)"), "knowledge.read"),
    (re.compile(r"^/api/reports/export$"), "formulation.read"),
    (re.compile(r"^/api/sources/export$"), "knowledge.read"),
    # SureChEMBL — extract is compute; confirm / KG ingest persist.
    (re.compile(r"^/api/surechembl/extract-example-draft$"), "knowledge.read"),
    (re.compile(r"^/api/surechembl/(confirm-example-draft|kg/ingest-document)$"), "knowledge.write"),
    # NotebookLM config persists an external-integration config.
    (re.compile(r"^/api/notebooklm/config$"), "admin.llm"),
    # Ops cleanup is a platform-admin action (no dedicated ops permission).
    (re.compile(r"^/api/ops(/|$)"), "admin.llm"),
    # PLM — batch QC results are QC data; the rest is PLM master data /
    # document writes. (Documents approve / packages / revisions submit /
    # spec / label-templates are endpoint-enforced below.)
    (re.compile(r"^/api/plm/batches(/|$)"), "qc.write"),
    (re.compile(r"^/api/plm/ghs(/|$)"), "plm.document.write"),
    (re.compile(r"^/api/plm/label-templates/preview$"), "plm.document.read"),
    (re.compile(r"^/api/plm/products(/|$)"), "plm.document.write"),
    (re.compile(r"^/api/plm/revisions/[^/]+/documents$"), "plm.document.write"),
    (re.compile(r"^/api/plm/templates$"), "plm.document.write"),
    (re.compile(r"^/api/plm/substances(/|$)"), "knowledge.write"),
    (re.compile(r"^/api/plm/regulatory/ask$"), "knowledge.read"),
    # Wiki — report / export / lint / preflight-review generate a view;
    # everything else persists wiki state or rebuilds an index.
    (re.compile(r"^/api/wiki/(dossier/report|storm/report|export|lint/run|preflight/review)"), "knowledge.read"),
    (re.compile(r"^/api/wiki(/|$)"), "knowledge.write"),
]

# Sensitive reads: GETs that need a permission (everything else stays open).
_SENSITIVE_READS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/api/identity/users$"), "admin.users"),
    (re.compile(r"^/api/identity/roles$"), "admin.users"),
]

# (path pattern, permission-or-note). The endpoint itself enforces these —
# the guard must NOT also apply a rule here (double enforcement can
# contradict OR-logic endpoints).
ENDPOINT_ENFORCED: list[tuple[re.Pattern[str], str]] = [
    # Lab — per-endpoint permissions (amend is lab.execute OR lab.review).
    (re.compile(r"^/api/lab/eln/conflicts/[^/]+/resolve$"), "lab.review"),
    (re.compile(r"^/api/lab/eln/[^/]+/pull$"), "lab.review"),
    (re.compile(r"^/api/lab/eln/[^/]+/push$"), "doe.write"),
    (re.compile(r"^/api/lab/instruments(/|$)"), "lab.review"),
    (re.compile(r"^/api/lab/measurements/import$"), "lab.execute"),
    (re.compile(r"^/api/lab/measurements/[^/]+/amend$"), "lab.execute OR lab.review"),
    (re.compile(r"^/api/lab/measurements/[^/]+/pick$"), "lab.review"),
    (re.compile(r"^/api/lab/measurements/[^/]+/review$"), "lab.review"),
    (re.compile(r"^/api/lab/methods(/|$)"), "lab.review"),
    (re.compile(r"^/api/lab/tasks$"), "doe.write"),
    (re.compile(r"^/api/lab/tasks/dispatch$"), "doe.write"),
    (re.compile(r"^/api/lab/tasks/[^/]+/cancel$"), "doe.write"),
    (re.compile(r"^/api/lab/tasks/[^/]+/claim$"), "lab.execute"),
    (re.compile(r"^/api/lab/tasks/[^/]+/measurements$"), "lab.execute"),
    (re.compile(r"^/api/lab/tasks/[^/]+/submit$"), "lab.execute"),
    # Signoff — per-endpoint / OR-logic permissions.
    (re.compile(r"^/api/plm/documents/[^/]+/approve$"), "coa.sign OR plm.document.review"),
    (re.compile(r"^/api/plm/packages/[^/]+/publish$"), "release.publish"),
    (re.compile(r"^/api/plm/packages/[^/]+/sign$"), "release.sign OR release.publish"),
    (re.compile(r"^/api/plm/packages/[^/]+/withdraw$"), "release.withdraw"),
    (re.compile(r"^/api/plm/revisions/[^/]+/candidate$"), "formulation.freeze"),
    (re.compile(r"^/api/plm/revisions/[^/]+/gate/waivers$"), "WAIVABLE (waiver policy)"),
    (re.compile(r"^/api/plm/revisions/[^/]+/submit$"), "release.submit"),
    (re.compile(r"^/api/plm/revisions/[^/]+/spec$"), "spec.write"),
    # Identity — user administration.
    (re.compile(r"^/api/identity/users$"), "admin.users"),
    (re.compile(r"^/api/identity/users/[^/]+/roles$"), "admin.users"),
    # PLM label-templates — plm.document.write OR admin.templates (OR logic).
    (re.compile(r"^/api/plm/label-templates$"), "plm.document.write OR admin.templates"),
    # Task cancel — ownership (assert_owner), not a permission.
    (re.compile(r"^/api/tasks/[^/]+/cancel$"), "ownership (assert_owner)"),
]

# Endpoint-enforced sensitive reads (the endpoint itself calls require()).
ENDPOINT_SENSITIVE_READS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/api/plm/audit$"), "audit.read"),
]

# (path pattern, reason). Deliberately no permission — verified by the audit
# so an exemption is never mistaken for a gap.
POLICY_EXEMPT: list[tuple[re.Pattern[str], str]] = [
    # Auth flows establish the session; they cannot require a permission.
    (re.compile(r"^/api/identity/(login|logout|refresh|password)$"), "auth flow"),
    (re.compile(r"^/api/notebooklm/login$"), "external auth flow"),
    # Conversational interface — the actions it triggers are gated separately.
    (re.compile(r"^/api/chat(/|$)"), "conversational interface"),
    (re.compile(r"^/api/session-plans(/|$)"), "conversational session state"),
    (re.compile(r"^/api/session(/|$)"), "conversational session state"),
    # Per-session safety decisions (same class as admin_guard's approve-session).
    (re.compile(r"^/api/mcp/approvals/[^/]+/decide$"), "per-session safety decision"),
    # admin_guard exemptions (per-chat actions every user takes while working).
    (re.compile(r"^/api/connectors/builtin/[^/]+/search$"), "admin_guard exempt: per-chat search"),
    (re.compile(r"^/api/connectors/mcp/call$"), "admin_guard exempt: approved MCP tool call"),
    (re.compile(r"^/api/connectors/mcp/[^/]+/approve-session$"), "admin_guard exempt: per-chat approval"),
]


# Service-key (operator credential) exemption — the whitelist for 1-15.
#
# A Bearer token that is NOT a personal access token (JWT) is the platform's
# operator credential (``FORMUNEX_API_TOKEN`` / ``FORMUNEX_API_TOKENS_JSON``).
# It is exempt from the personal permission checks above, for three reasons:
# 1. It IS the platform operator: the frontend, CI and automation act through
#    it. It has no user identity and no roles, so there is no principal to
#    check — a permission check on it would be a check against nothing.
# 2. It is NOT unchecked: the auth middleware (``middleware/api_auth.py``)
#    rejects any Bearer token that is neither the configured operator
#    credential nor a valid personal access token, before the request reaches
#    any endpoint. Only the configured credential reaches this guard.
# 3. Personal actions already reject it: endpoints that record *who* did
#    something (sign-off, e-signatures, waivers, account administration)
#    depend on ``current_user``, which returns 401 for a service key
#    ("服务 API Key 不能代表个人操作").
#
# Scoped service keys (a per-key permission set) do NOT exist yet — that is a
# separate feature (key management + scoping), not a Phase 1D gap fix. If a
# deployment needs to restrict automation, it must use personal access
# tokens (which ARE checked) instead of the operator credential.
SERVICE_KEY_EXEMPT_REASON = (
    "operator credential (FORMUNEX_API_TOKEN): validated by the auth middleware, "
    "represents the platform not a person; personal actions reject it via current_user"
)


def required_permission(method: str, path: str) -> tuple[str | None, str | None]:
    """Return (permission, project_id) for a gated request, else (None, None)."""
    m = method.upper()
    if m in _WRITE:
        for pattern, permission in _RULES:
            if pattern.match(path):
                project_id = None
                parts = path.split("/")
                # /api/projects/{project_id}[...] → parts = ["", "api", "projects", id, ...]
                if len(parts) > 3 and parts[1] == "api" and parts[2] == "projects" and parts[3]:
                    project_id = parts[3]
                return permission, project_id
        return None, None
    if m == "GET":
        for pattern, permission in _SENSITIVE_READS:
            if pattern.match(path):
                return permission, None
    return None, None


def rbac_guard(request: HTTPConnection) -> None:
    # HTTPConnection (not Request): FastAPI only fills a ``Request``-typed
    # dependency param when the connection *is* a Request, so a WebSocket
    # route would call this with no args. HTTPConnection is the common base of
    # Request and WebSocket, and the guard only needs method / url / headers.
    # WebSocket has no .method: treat it as a read (never blocked here).
    method = getattr(request, "method", "GET")
    permission, project_id = required_permission(method, request.url.path)
    if permission is None:
        return
    from ..services.identity import tokens

    token = _bearer(request)
    # Only a personal access token is checked here. A service key is the
    # operator credential — see SERVICE_KEY_EXEMPT_REASON (validated by the
    # auth middleware; personal actions reject it via current_user).
    if token and tokens.looks_like_access_token(token):
        current_user(request).require(permission, project_id)
