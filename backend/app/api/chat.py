"""POST /api/chat — Q&A grounded in loaded sources (Chat P0)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import PrivateAttr, field_validator

from ..domain.chat_schemas import ChatRequest, ChatResponse, ChatTurn
from ..domain.schemas import Evidence
from ..services.chat_orchestration import (
    clamp_relevance,
    sanitize_evidence,
    run_chat,
    stream_chat_events,
)

logger = logging.getLogger(__name__)

router = APIRouter()




class ChatRequestValidated(ChatRequest):
    # Who sent it: set by the route from the bearer token, never read from the body (a client must not name its own
    # owner). ``None`` = an internal call with no identity, which the Text2SQL scope treats as "restrict nothing".
    _owner_id: str | None = PrivateAttr(default=None)
    # Notices from deep mode (PageIndex unavailable / failed / empty), merged into the answer's ``notices``.
    _deep_notices: list = PrivateAttr(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, raw: object) -> object:
        if not isinstance(raw, list):
            return raw
        # Cap the number of supplied sources to bound prompt size / cost.
        # NOTE: ChatRequest.sources lives in domain/chat_schemas.py (outside
        # this agent's file list); the hard Field(max_length=50) constraint
        # should be added there when that file is touched.
        if len(raw) > 50:
            logger.warning("chat sources truncated: %d -> 50", len(raw))
            raw = raw[:50]
        out: list[dict] = []
        dropped = 0
        for item in raw:
            if isinstance(item, Evidence):
                out.append(sanitize_evidence(item).model_dump())
            elif isinstance(item, dict):
                data = dict(item)
                if "relevance" in data:
                    data["relevance"] = clamp_relevance(data.get("relevance", 0.5))
                try:
                    out.append(sanitize_evidence(Evidence.model_validate(data)).model_dump())
                except Exception:
                    dropped += 1
                    continue
        if dropped:
            logger.warning("chat sources: %d invalid item(s) discarded", dropped)
        return out

    @field_validator("history", mode="before")
    @classmethod
    def _coerce_history(cls, raw: object) -> object:
        if not isinstance(raw, list):
            return raw

        # P2-2: validator 只做超大保护（200 条），精细截断（token budget+摘要）留给 trim_history
        hard_cap = 200
        items = raw[-hard_cap:] if len(raw) > hard_cap else raw
        out: list[dict] = []
        for item in items:
            if isinstance(item, ChatTurn):
                out.append(item.model_dump())
            elif isinstance(item, dict):
                try:
                    out.append(ChatTurn.model_validate(item).model_dump())
                except Exception:
                    continue
        return out




def _bind_owner(req: ChatRequestValidated, request: Request | None) -> None:
    """Record the caller's owner on the request so the structured (Text2SQL) half reads only what they may see."""
    if request is not None:
        from ..middleware.api_auth import get_current_owner

        req._owner_id = get_current_owner(request)




def _chat_caller_id(request) -> str | None:
    """Real login id of a JWT chat caller, else None (Stage 0 / SR-006).

    Deliberately NOT ``get_current_owner``: that falls back to ``"default"``
    for JWT users (SR-014, fixed in stage 2 by D-3), and a ``"default"``
    caller would fail every owner check and break legitimate JWT chat MCP
    use. Fail-soft: an invalid/revoked token or identity-less caller yields
    None, i.e. the legacy unchecked behavior, never a 401 for the chat.
    """
    if request is None:
        return None
    try:
        from ._principal import optional_user

        user = optional_user(request)
        return user.id if user is not None else None
    except HTTPException:
        # Stage 2 / SR-011: a JWT-shaped token that fails validation
        # (revoked/expired) must not degrade to the unchecked legacy
        # path — revocation takes effect immediately. Absent tokens and
        # service keys still yield None: optional_user returns None for
        # those before any validation runs.
        raise
    except Exception:  # noqa: BLE001
        return None

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequestValidated, request: Request = None):  # type: ignore[assignment]
    """Thin route (stage 4 / SR-021): auth glue + service call + HTTP mapping."""
    _bind_owner(req, request)
    try:
        return run_chat(req, _chat_caller_id(request))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="问答处理失败") from exc


@router.post("/chat/stream")
async def chat_stream(req: ChatRequestValidated, request: Request = None):  # type: ignore[assignment]
    """Thin route (stage 4 / SR-021): auth glue + SSE wrap."""
    _bind_owner(req, request)
    return StreamingResponse(
        stream_chat_events(req, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
