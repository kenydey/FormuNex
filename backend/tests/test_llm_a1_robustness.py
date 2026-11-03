"""Three-library A1: LLM robustness additions in app/services/llm.py.

Covers loads_lenient (bare-backslash SMILES repair), dump_unparsable
(diagnostics落盘), and the 5xx-precise retry classification (only 429 /
502 / 503 / 504 retry; other known statuses fail immediately).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import llm


class _StatusErr(Exception):
    def __init__(self, code: int):
        self.status_code = code
        super().__init__(f"Error code: {code}")


def test_loads_lenient_valid_json_unchanged():
    assert llm.loads_lenient('{"a": "x\\\\y\\n\\"q\\""}') == {"a": 'x\\y\n"q"'}


def test_loads_lenient_repairs_bare_backslash_smiles():
    # Model-written SMILES with a bare backslash: plain json.loads dies.
    raw = '{"smiles": "C=C(\\[2*])"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert llm.loads_lenient(raw) == {"smiles": "C=C(\\[2*])"}


def test_loads_lenient_still_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        llm.loads_lenient('{"a": }')


def test_dump_unparsable_writes_only_reply_text(tmp_path, monkeypatch):
    # Redirect the dump root by pointing __file__-derived path: simplest is
    # to call the real function and clean up what it created.
    out = llm.dump_unparsable('{"broken": ', tag="a/b:test")
    assert out is not None
    path = Path(out)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == '{"broken": '
    assert "/" not in path.name  # tag sanitised, no subdirectories
    path.unlink()
    if not any(path.parent.iterdir()):
        path.parent.rmdir()


@pytest.mark.parametrize("code", [429, 502, 503, 504])
def test_classify_retryable_statuses(code):
    err = llm._classify_upstream_error(_StatusErr(code))
    assert isinstance(err, llm.LLMTransientError)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422, 500])
def test_classify_non_retryable_statuses(code):
    # 401/403 are auth (handled before the classifier) but must never be
    # classified as transient if they ever reach it.
    err = llm._classify_upstream_error(_StatusErr(code))
    assert isinstance(err, llm.LLMNonRetryableError)


def test_classify_message_only_status():
    assert isinstance(
        llm._classify_upstream_error(Exception("Error code: 503")),
        llm.LLMTransientError,
    )
    assert isinstance(
        llm._classify_upstream_error(Exception("404 Not Found")),
        llm.LLMNonRetryableError,
    )


def test_classify_transport_and_unknown():
    assert isinstance(
        llm._classify_upstream_error(TimeoutError("t")), llm.LLMTransientError
    )
    assert isinstance(
        llm._classify_upstream_error(ConnectionError("c")), llm.LLMTransientError
    )
    # Unclassifiable errors stay retryable (fail-open, historical behaviour).
    assert isinstance(
        llm._classify_upstream_error(Exception("boom")), llm.LLMTransientError
    )
    httpx_style = SimpleNamespace(response=SimpleNamespace(status_code=502))
    assert isinstance(
        llm._classify_upstream_error(httpx_style), llm.LLMTransientError
    )


def _install_openai_stub():
    previous = sys.modules.get("openai")
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub
    return previous


def _restore_openai_module(previous):
    if previous is None:
        sys.modules.pop("openai", None)
    else:
        sys.modules["openai"] = previous


def _detail_with_status(code: int):
    previous = _install_openai_stub()
    try:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = _StatusErr(code)
        with patch("openai.OpenAI", return_value=mock_client):
            text, err = llm._complete_openai_compatible_detail(
                "hello", "test-key", "gpt-4o-mini", 16, None
            )
        return text, err, mock_client.chat.completions.create.call_count
    finally:
        _restore_openai_module(previous)


def test_detail_does_not_retry_known_4xx():
    text, err, calls = _detail_with_status(400)
    assert text is None and err is not None
    assert calls == 1


def test_detail_retries_gateway_503_once():
    text, err, calls = _detail_with_status(503)
    assert text is None and err is not None
    assert calls == 2  # _LLM_RETRY: initial + 1 retry


def test_complete_json_lenient_smiles(monkeypatch):
    monkeypatch.setattr(
        llm, "call_llm", lambda *a, **k: '```json\n{"smiles": "C=C(\\[2*])"}\n```'
    )
    assert llm.complete_json("x") == {"smiles": "C=C(\\[2*])"}


def test_complete_json_unparsable_dumps_and_returns_none(monkeypatch):
    monkeypatch.setattr(llm, "call_llm", lambda *a, **k: "not json at all {")
    assert llm.complete_json("x") is None
    dump_dir = Path(llm.__file__).resolve().parent.parent / "data" / "llm_unparsable"
    dumped = sorted(dump_dir.glob("*-complete_json-*.txt"))
    assert dumped
    assert dumped[-1].read_text(encoding="utf-8") == "not json at all {"
    dumped[-1].unlink()
