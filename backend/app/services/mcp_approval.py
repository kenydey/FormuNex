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
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import McpApprovalRequestRow, McpToolPolicyRow
from ..db.session_utils import commit_session

logger = logging.getLogger(__name__)

DECISIONS = ("allow", "ask", "block")
SCOPES = ("once", "session", "project", "global")

# ask 挂起超时：5 分钟无决策自动 deny。用单调时钟，不起常驻线程。
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
    """多作用域决策合并：block > ask > allow；无策略默认 ask。"""
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


def _pending_in_session(session: Session, sid: str, tool: str) -> McpApprovalRequestRow | None:
    return session.execute(
        select(McpApprovalRequestRow)
        .where(
            McpApprovalRequestRow.server_id == sid,
            McpApprovalRequestRow.tool_name == tool,
            McpApprovalRequestRow.status == "pending",
        )
        .order_by(McpApprovalRequestRow.requested_at.desc())
        .limit(1)
    ).scalar_one_or_none()


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
    what the call would do (the latest attempt's arguments replace those of a still-pending request).

    返回 {"verdict": "allow"} 或
    {"verdict": "deny", "reason": ..., "request_id": ...}。
    ``now`` 为单调时钟时间戳（测试注入用），默认 ``time.monotonic()``。
    本函数不抛业务异常；DB 异常向上传播，由调用方按 ask(deny) 处理。
    """
    sid, tool = _norm(server_id, tool_name)
    if not sid or not tool:
        return {"verdict": "deny", "reason": "invalid_tool_identity"}
    mono = time.monotonic() if now is None else now
    with commit_session(_session_factory(session_factory)) as session:
        eff = effective_decision(_policies_in_session(session, sid, tool))
        if eff == "allow":
            return {"verdict": "allow"}
        if eff == "block":
            _audit("policy_block", sid, tool)
            return {"verdict": "deny", "reason": "policy_block"}

        # eff == "ask"：查挂起的审批请求
        pending = _pending_in_session(session, sid, tool)
        if pending is None:
            request_id = str(uuid.uuid4())
            session.add(
                McpApprovalRequestRow(
                    id=request_id, server_id=sid, tool_name=tool, session_id=session_id,
                    project_id=project_id, status="pending", requested_at=mono,
                    arguments_json=_arguments_json(arguments),
                )
            )
            _audit("approval_required", sid, tool, request_id=request_id)
            return {"verdict": "deny", "reason": "approval_required", "request_id": request_id}
        age = mono - float(pending.requested_at)
        if age >= ASK_TIMEOUT_S:
            pending.status = "expired"
            pending.decided_at = mono
            _audit("approval_timeout", sid, tool, request_id=pending.id, age_s=round(age, 1))
            return {"verdict": "deny", "reason": "approval_timeout", "request_id": pending.id}
        if arguments is not None:
            pending.arguments_json = _arguments_json(arguments)
        _audit("approval_pending", sid, tool, request_id=pending.id, age_s=round(age, 1))
        return {"verdict": "deny", "reason": "approval_pending", "request_id": pending.id}


def decide_approval(
    request_id: str,
    decision: str,
    *,
    scope: str = "once",
    session_factory: sessionmaker[Session] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """对挂起的审批请求做决策（供前端审批 UI 调用）。

    allow → 按 scope 落一条 allow 策略；deny → 仅标记请求，不持久化拒绝。
    """
    if decision not in ("allow", "deny"):
        raise ValueError(f"decision 必须是 allow/deny，得到 {decision!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope 必须是 {SCOPES} 之一，得到 {scope!r}")
    mono = time.monotonic() if now is None else now
    with commit_session(_session_factory(session_factory)) as session:
        row = session.get(McpApprovalRequestRow, request_id)
        if row is None:
            return {"ok": False, "error": "request_not_found"}
        if row.status != "pending":
            return {"ok": False, "error": f"request_not_pending: {row.status}"}
        row.status = "approved" if decision == "allow" else "denied"
        row.decided_at = mono
        if decision == "allow":
            _set_policy_in_session(session, row.server_id, row.tool_name, "allow", scope, now=time.time())
    _audit("approval_decided", row.server_id, row.tool_name, request_id=request_id, decision=decision, scope=scope)
    return {"ok": True, "request_id": request_id, "decision": decision, "scope": scope}


def list_pending(
    *,
    session_factory: sessionmaker[Session] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return currently pending MCP tool approval requests, with their (redacted) tool-call arguments.

    Requests older than ``ASK_TIMEOUT_S`` are not actionable (the next
    ``evaluate_call`` expires them) and are excluded.
    """
    mono = time.monotonic() if now is None else now
    with _session_factory(session_factory)() as session:
        rows = session.execute(
            select(McpApprovalRequestRow)
            .where(McpApprovalRequestRow.status == "pending")
            .order_by(McpApprovalRequestRow.requested_at.asc())
        ).scalars()
        items: list[dict[str, Any]] = []
        for row in rows:
            age = mono - float(row.requested_at)
            if age >= ASK_TIMEOUT_S:
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
