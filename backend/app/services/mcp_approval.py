"""MCP 逐工具审批三态（W2-7 / P1-3）。

Allow / Ask / Block 按工具粒度门禁，决策按 once / session / project / global
作用域持久化到主数据库（Phase 2D 2-24 起：``mcp_tool_policies`` /
``mcp_approval_requests``，alembic 0062 建表；旧的独立 ``mcp_approval.db``
SQLite 文件不再使用），默认全部 ask。

仓库内无现成的 approval card / 审批机制（2026-09-27 核查结论），因此：
ask 无决策时后端默认 deny 并写审计日志，request_id 留给前端审批 UI；
审批模块自身异常时按 ask(deny) 处理，绝不静默放行。

All functions take a ``session_factory`` (default: the app's main-DB
factory) instead of a SQLite file path. ``evaluate_call`` / ``decide_approval``
each run in ONE ``commit_session`` transaction.

W0 语义（2026-10-27，深度审查修复单批）：
- 墙钟：``requested_at`` / ``decided_at`` 是墙钟秒（``time.time()``），不再是
  单调时钟。单调时钟在进程 / 系统重启后重置，旧挂起行会算出负 age 而永不过期；
  墙钟下存量单调时钟行算出巨大 age，在决策点与求值点一律按过期处理，永不复活。
- 作用域：block / ask 在任何作用域都生效（fail-closed）；allow 只在调用方归属
  相符时生效——global / once 恒生效，session 要求调用带 session_id，project
  要求调用带 project_id。无归属的调用对 session / project allow 一律走 ask。
- once 真消耗：once allow 在首次实际放行时从策略表删除，第二次同工具调用
  重新进入审批。
- 挂起隔离：挂起请求按 (server, tool, session, project) 隔离；后一次调用不再
  覆盖前一次待审批的参数，审批人批的永远是最初那次调用。
- 归属：decide / list 的 actor 由 API 层传入（current_user）；actor=None 仅用于
  服务内直调与测试，跳过归属校验——生产调用方必须传 actor。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import ChatSessionRow, McpApprovalRequestRow, McpToolPolicyRow
from ..db.session_utils import commit_session
from .audit import hash_chain

logger = logging.getLogger(__name__)

DECISIONS = ("allow", "ask", "block")
SCOPES = ("once", "session", "project", "global")

# ask 挂起超时：5 分钟无决策自动 deny。用墙钟（见模块 docstring），不起常驻线程。
ASK_TIMEOUT_S = 300.0

_SECRET_KEY_RE = re.compile(r"token|key|password|passwd|secret|authorization|credential|cookie", re.IGNORECASE)
REDACTED = "***"
_MAX_ARGS_JSON = 20_000


def _session_factory(session_factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    if session_factory is not None:
        return session_factory
    from ..db.database import default_session_factory

    return default_session_factory()


def redact_arguments(value: Any, _depth: int = 0) -> Any:
    """Copy of tool-call arguments with the values of secret-looking keys (token, key, password, secret,
    authorization …) replaced by ``***`` — recursively through dicts and lists."""
    if _depth > 20:
        return REDACTED
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if _SECRET_KEY_RE.search(str(k)) else redact_arguments(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_arguments(v, _depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _arguments_json(arguments: dict[str, Any] | None) -> str | None:
    if arguments is None:
        return None
    try:
        text = json.dumps(redact_arguments(arguments), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    if len(text) > _MAX_ARGS_JSON:
        text = json.dumps({"_truncated": True, "preview": text[:_MAX_ARGS_JSON]}, ensure_ascii=False)
    return text


def load_arguments(raw: str | None) -> dict[str, Any] | None:
    """Stored (already redacted) arguments → dict; redacted again in case a row predates redaction."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return redact_arguments(data) if isinstance(data, dict) else {"value": redact_arguments(data)}


def _norm(server_id: str, tool_name: str) -> tuple[str, str]:
    return str(server_id or "").strip(), str(tool_name or "").strip()


def _set_policy_in_session(
    session: Session, server_id: str, tool_name: str, decision: str, scope: str, *, now: float
) -> None:
    row = session.get(McpToolPolicyRow, (server_id, tool_name, scope))
    if row is None:
        session.add(
            McpToolPolicyRow(
                server_id=server_id, tool_name=tool_name, scope=scope,
                decision=decision, created_at=now, updated_at=now,
            )
        )
    else:
        row.decision = decision
        row.updated_at = now


def set_policy(
    server_id: str,
    tool_name: str,
    decision: str,
    scope: str = "global",
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any]:
    """写入一条策略。decision 非法或 scope 非法时抛 ValueError。"""
    if decision not in DECISIONS:
        raise ValueError(f"decision 必须是 {DECISIONS} 之一，得到 {decision!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope 必须是 {SCOPES} 之一，得到 {scope!r}")
    sid, tool = _norm(server_id, tool_name)
    if not sid or not tool:
        raise ValueError("server_id / tool_name 不能为空")
    with commit_session(_session_factory(session_factory)) as session:
        _set_policy_in_session(session, sid, tool, decision, scope, now=time.time())
    return {"ok": True, "server_id": sid, "tool_name": tool, "scope": scope, "decision": decision}


def get_policies(
    server_id: str,
    tool_name: str,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, str]:
    """返回 {scope: decision}；无策略时返回 {}（调用方按默认 ask 处理）。"""
    sid, tool = _norm(server_id, tool_name)
    with _session_factory(session_factory)() as session:
        rows = session.execute(
            select(McpToolPolicyRow).where(
                McpToolPolicyRow.server_id == sid, McpToolPolicyRow.tool_name == tool
            )
        ).scalars()
        return {row.scope: row.decision for row in rows}


def effective_decision(policies: dict[str, str]) -> str:
    """多作用域决策合并：block > ask > allow；无策略默认 ask。

    注意：这是作用域无关的纯合并。``evaluate_call`` 在此之上对 allow 再做
    归属过滤（session / project allow 只对带对应归属的调用生效）。"""
    vals = set(policies.values())
    if "block" in vals:
        return "block"
    if "ask" in vals:
        return "ask"
    if "allow" in vals:
        return "allow"
    return "ask"


def _audit(event: str, sid: str, tool: str, **fields: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.warning("MCP 审批审计 event=%s server=%s tool=%s %s", event, sid, tool, detail)


def _policies_in_session(session: Session, sid: str, tool: str) -> dict[str, str]:
    rows = session.execute(
        select(McpToolPolicyRow).where(
            McpToolPolicyRow.server_id == sid, McpToolPolicyRow.tool_name == tool
        )
    ).scalars()
    return {row.scope: row.decision for row in rows}


def _pending_in_session(
    session: Session, sid: str, tool: str, session_id: str | None, project_id: str | None
) -> McpApprovalRequestRow | None:
    """The pending request for exactly this (server, tool, session, project).

    ``== None`` renders as ``IS NULL``: unattributed calls only match unattributed
    requests. A later call from another session/project never sees — let alone
    overwrites — this session's pending request.
    """
    return session.execute(
        select(McpApprovalRequestRow)
        .where(
            McpApprovalRequestRow.server_id == sid,
            McpApprovalRequestRow.tool_name == tool,
            McpApprovalRequestRow.session_id == session_id,
            McpApprovalRequestRow.project_id == project_id,
            McpApprovalRequestRow.status == "pending",
        )
        .order_by(McpApprovalRequestRow.requested_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@dataclass(frozen=True)
class Actor:
    """The deciding/listing user, as resolved by the API layer from current_user.

    ``project_ids`` are the projects the user is a member of (from the
    principal's project permissions). ``is_admin`` is ``can("admin.connectors")``.
    """

    user_id: str
    is_admin: bool = False
    project_ids: frozenset[str] = frozenset()


def _can_decide(session: Session, row: McpApprovalRequestRow, actor: Actor) -> bool:
    """May ``actor`` decide (and therefore see) this pending request?

    Fail-closed: admins may decide everything; otherwise the request must be
    attributable to one of the actor's projects — directly via ``project_id``,
    or via the chat session the request was raised from (chat sessions are
    project-scoped). Unattributed requests, and requests whose session row is
    gone, are admin-only.
    """
    if actor.is_admin:
        return True
    if row.project_id is not None and row.project_id in actor.project_ids:
        return True
    if row.session_id is not None:
        chat = session.get(ChatSessionRow, row.session_id)
        if chat is not None and chat.project_id is not None and chat.project_id in actor.project_ids:
            return True
    return False


def _applicable_allow(policies: dict[str, str], session_id: str | None, project_id: str | None) -> bool:
    """Is there an allow policy that applies to this (attributed or not) call?

    global / once always apply; session applies only to session-attributed calls,
    project only to project-attributed calls. Block / ask are NOT gated here —
    they stay scope-agnostic (fail-closed) and are checked before this.
    """
    if policies.get("global") == "allow" or policies.get("once") == "allow":
        return True
    if session_id is not None and policies.get("session") == "allow":
        return True
    if project_id is not None and policies.get("project") == "allow":
        return True
    return False


def evaluate_call(
    server_id: str,
    tool_name: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: float | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """审批门禁求值。

    ``arguments``: the tool call's arguments, stored redacted on the approval request so the approver sees
    what the call would do. A later call never overwrites a still-pending request's
    arguments: the approver decides the original call, not a replaced one.

    返回 {"verdict": "allow"} 或
    {"verdict": "deny", "reason": ..., "request_id": ...}。
    ``now`` 为墙钟时间戳（测试注入用），默认 ``time.time()``。
    本函数不抛业务异常；DB 异常向上传播，由调用方按 ask(deny) 处理。
    """
    sid, tool = _norm(server_id, tool_name)
    if not sid or not tool:
        return {"verdict": "deny", "reason": "invalid_tool_identity"}
    wall = time.time() if now is None else now
    with commit_session(_session_factory(session_factory)) as session:
        policies = _policies_in_session(session, sid, tool)
        vals = set(policies.values())
        if "block" in vals:
            _audit("policy_block", sid, tool)
            return {"verdict": "deny", "reason": "policy_block"}
        if "ask" in vals or not _applicable_allow(policies, session_id, project_id):
            # ask 路径：查本 (server, tool, session, project) 的挂起请求
            pending = _pending_in_session(session, sid, tool, session_id, project_id)
            if pending is None:
                request_id = str(uuid.uuid4())
                session.add(
                    McpApprovalRequestRow(
                        id=request_id, server_id=sid, tool_name=tool, session_id=session_id,
                        project_id=project_id, status="pending", requested_at=wall,
                        arguments_json=_arguments_json(arguments),
                    )
                )
                _audit("approval_required", sid, tool, request_id=request_id)
                return {"verdict": "deny", "reason": "approval_required", "request_id": request_id}
            age = wall - float(pending.requested_at)
            if age >= ASK_TIMEOUT_S:
                pending.status = "expired"
                pending.decided_at = wall
                _audit("approval_timeout", sid, tool, request_id=pending.id, age_s=round(age, 1))
                return {"verdict": "deny", "reason": "approval_timeout", "request_id": pending.id}
            _audit("approval_pending", sid, tool, request_id=pending.id, age_s=round(age, 1))
            return {"verdict": "deny", "reason": "approval_pending", "request_id": pending.id}
        # allow 路径：once 策略在此次放行中真消耗，同一事务内删除。
        if policies.get("once") == "allow":
            once_row = session.get(McpToolPolicyRow, (sid, tool, "once"))
            if once_row is not None:
                session.delete(once_row)
        return {"verdict": "allow"}


def decide_approval(
    request_id: str,
    decision: str,
    *,
    scope: str = "once",
    session_factory: sessionmaker[Session] | None = None,
    now: float | None = None,
    actor: Actor | None = None,
) -> dict[str, Any]:
    """对挂起的审批请求做决策（供前端审批 UI 调用）。

    allow → 按 scope 落一条 allow 策略；deny → 仅标记请求，不持久化拒绝。
    决策点强制执行 5 分钟窗口：超龄请求标记 ``expired`` 并拒绝，且不论调用方
    身份；存量单调时钟行在墙钟下算出巨大 age，同样按过期处理，永不复活批准。
    ``actor`` 由 API 层传入（None 仅用于服务内直调与测试，跳过归属校验）：
    scope=global 仅限管理员；session / project 作用域要求请求本身带对应归属；
    非管理员只能裁决自己项目的请求（直接归属，或经由请求会话的项目归属）。
    归属校验对 allow 与 deny 一律生效；scope 校验只对 allow 生效（deny 不写
    策略，scope 无意义，不应成为拒绝 deny 的理由）。
    决策连同 actor 一并写入哈希审计链（与业务写同一事务，原子提交）。
    """
    if decision not in ("allow", "deny"):
        raise ValueError(f"decision 必须是 allow/deny，得到 {decision!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope 必须是 {SCOPES} 之一，得到 {scope!r}")
    wall = time.time() if now is None else now
    with commit_session(_session_factory(session_factory)) as session:
        row = session.get(McpApprovalRequestRow, request_id)
        if row is None:
            return {"ok": False, "error": "request_not_found"}
        if row.status != "pending":
            return {"ok": False, "error": f"request_not_pending: {row.status}"}
        age = wall - float(row.requested_at)
        if age >= ASK_TIMEOUT_S:
            row.status = "expired"
            row.decided_at = wall
            _audit("approval_expired_on_decide", row.server_id, row.tool_name,
                   request_id=request_id, age_s=round(age, 1))
            return {"ok": False, "error": "request_expired"}
        if actor is not None:
            # Attribution binds every decision (deny included: a stranger must
            # not be able to kill someone else's request either).
            if not _can_decide(session, row, actor):
                return {"ok": False, "error": "not_authorized"}
            # Scope only matters for allow (deny writes no policy), so scope
            # validation runs for allow only: denying with scope=session on a
            # project-only request must not be refused for a meaningless scope.
            if decision == "allow":
                if scope == "global" and not actor.is_admin:
                    return {"ok": False, "error": "not_authorized"}
                if scope == "session" and row.session_id is None:
                    return {"ok": False, "error": "scope_requires_session"}
                if scope == "project" and row.project_id is None:
                    return {"ok": False, "error": "scope_requires_project"}
        row.status = "approved" if decision == "allow" else "denied"
        row.decided_at = wall
        if decision == "allow":
            _set_policy_in_session(session, row.server_id, row.tool_name, "allow", scope, now=wall)
        if actor is not None:
            hash_chain.append(
                session, actor=actor.user_id, action="mcp.approval.decided",
                entity_type="mcp_approval_request", entity_id=request_id,
                detail={"decision": decision, "scope": scope,
                        "server_id": row.server_id, "tool_name": row.tool_name},
            )
    _audit("approval_decided", row.server_id, row.tool_name, request_id=request_id, decision=decision, scope=scope)
    return {"ok": True, "request_id": request_id, "decision": decision, "scope": scope}


def list_pending(
    *,
    session_factory: sessionmaker[Session] | None = None,
    now: float | None = None,
    actor: Actor | None = None,
) -> dict[str, Any]:
    """Return currently pending MCP tool approval requests, with their (redacted) tool-call arguments.

    Requests older than ``ASK_TIMEOUT_S`` are not actionable (the next
    ``evaluate_call`` expires them) and are excluded. With an ``actor`` (the API
    always passes one), non-admins only see requests they may decide — other
    users' sessions and projects are invisible, not just undecidable.
    """
    wall = time.time() if now is None else now
    with _session_factory(session_factory)() as session:
        rows = session.execute(
            select(McpApprovalRequestRow)
            .where(McpApprovalRequestRow.status == "pending")
            .order_by(McpApprovalRequestRow.requested_at.asc())
        ).scalars()
        items: list[dict[str, Any]] = []
        for row in rows:
            age = wall - float(row.requested_at)
            if age >= ASK_TIMEOUT_S:
                continue
            if actor is not None and not _can_decide(session, row, actor):
                continue
            items.append(
                {
                    "request_id": row.id,
                    "server_id": row.server_id,
                    "tool_name": row.tool_name,
                    "session_id": row.session_id,
                    "project_id": row.project_id,
                    "age_s": round(age, 1),
                    # Tool-call arguments (secret-looking keys redacted); None when the call recorded none.
                    "arguments": load_arguments(row.arguments_json),
                }
            )
    return {"items": items, "total": len(items)}
