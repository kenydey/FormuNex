"""Stage 4 / SR-020: dispatch kernel lives in the services layer, not the API layer.

Gate tests (not behaviour tests — the 503/202 contract itself is covered by
test_dispatch_guard / test_p21_dispatch_order / test_w1_dispatch_claim):

* no service module imports ``app.api._dispatch`` (the two pre-move callers,
  workbench_loop and ocsr, plus pageindex summaries, now import the kernel);
* the kernel module imports no FastAPI at all, so background code can never
  observe an HTTP status from it;
* the kernel raises :class:`DispatchUnavailable` (a domain exception carrying
  detail + task id), never HTTPException;
* the API adapter maps exactly that exception to a 503 with the
  ``X-FormuNex-Task-Id`` header, preserving the W1-10 claim contract.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "app"


def _read(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


def test_no_service_module_imports_api_dispatch():
    """services/ must not import app.api._dispatch (SR-020's core rule).

    Matches import statements only: the kernel's own docstring mentions the
    adapter module by name, and that is documentation, not a dependency.
    """
    import re

    import_re = re.compile(
        r"^\s*(?:from\s+[\w.]*api\s+import\s+_dispatch"
        r"|from\s+[\w.]*api\._dispatch\s+import"
        r"|import\s+app\.api\._dispatch)\b",
        re.MULTILINE,
    )
    offenders = []
    for path in sorted((BACKEND / "services").rglob("*.py")):
        if import_re.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []


def test_kernel_imports_no_fastapi():
    text = _read("services/dispatch.py")
    assert "from fastapi" not in text
    assert "import fastapi" not in text
    assert "HTTPException" not in text
    assert "JSONResponse" not in text


def test_kernel_raises_domain_exception_not_http(monkeypatch, non_eager):
    """Broker down at the kernel level: DispatchUnavailable, id attached."""
    from app.services import dispatch as kernel

    monkeypatch.setattr(kernel, "broker_reachable", lambda: False)
    with pytest.raises(kernel.DispatchUnavailable) as excinfo:
        kernel.dispatch_task_kernel(object(), {"k": "v"}, "doe_cycle")
    exc = excinfo.value
    assert isinstance(exc.detail, str)
    assert exc.task_id
    assert not isinstance(exc, HTTPException)


def test_adapter_maps_kernel_failure_to_claimable_503(monkeypatch, non_eager):
    """Same outage through the API adapter: 503 + task-id header (W1-10)."""
    from app.api import _dispatch
    from app.services import dispatch as kernel

    monkeypatch.setattr(kernel, "broker_reachable", lambda: False)
    with pytest.raises(HTTPException) as excinfo:
        _dispatch.dispatch_task(object(), {"k": "v"}, "doe_cycle")
    exc = excinfo.value
    assert exc.status_code == 503
    assert isinstance(exc.detail, str)
    assert exc.headers["X-FormuNex-Task-Id"]


@pytest.fixture()
def non_eager(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    return settings
