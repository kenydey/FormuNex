"""Package A: project-scoped uploads, single-source re-parse, search-hit → KB source resolve."""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.source_store import get_source_store
from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def files_dir(tmp_path, monkeypatch):
    d = tmp_path / "source_files"
    d.mkdir()
    monkeypatch.setattr("app.services.source_files.source_files_dir", lambda: d)
    return d


def _wait(client, task_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task.get("state") in ("completed", "failed"):
            return task
        time.sleep(0.1)
    return task


def _upload(client, text: str, *, project_id: str | None = None, batch: bool = False) -> dict:
    data = {"project_id": project_id} if project_id is not None else None
    if batch:
        r = client.post(
            "/api/ingest/batch",
            files=[("files", ("a.txt", text.encode(), "text/plain")),
                   ("files", ("b.txt", (text + " second part").encode(), "text/plain"))],
            data=data,
        )
    else:
        r = client.post("/api/ingest", files={"file": ("note.txt", text.encode(), "text/plain")}, data=data)
    assert r.status_code == 202, r.text
    task = _wait(client, r.json()["task_id"])
    assert task.get("state") == "completed", task
    return task.get("result") or {}


def _text(tag: str) -> str:
    return (
        f"Epoxy zinc primer technical data sheet {tag}. Solids content 65 percent by volume, "
        f"salt spray resistance 720 hours, dry film thickness 80 micron. Batch {tag} notes."
    )


# ── 1. POST /api/ingest(/batch) project_id ──────────────────────────────────

def test_upload_with_project_id_is_project_scoped(client, files_dir):
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    result = _upload(client, _text(uuid.uuid4().hex), project_id=pid)
    row = get_source_store().get(result["source_id"])
    assert row is not None and row.project_id == pid


def test_upload_without_project_id_stays_global(client, files_dir):
    result = _upload(client, _text(uuid.uuid4().hex))
    assert get_source_store().get(result["source_id"]).project_id is None
    blank = _upload(client, _text(uuid.uuid4().hex), project_id="  ")
    assert get_source_store().get(blank["source_id"]).project_id is None


def test_batch_upload_with_project_id_scopes_every_file(client, files_dir):
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    tag = uuid.uuid4().hex
    result = _upload(client, _text(tag), project_id=pid, batch=True)
    assert result["files_processed"] == 2
    assert get_source_store().count_for_project(pid) == 2


def test_project_id_too_long_is_rejected(client):
    r = client.post("/api/ingest", files={"file": ("n.txt", b"hello world text", "text/plain")},
                    data={"project_id": "x" * 65})
    assert r.status_code == 422


# ── 2. POST /api/kb/sources/{id}/reparse ────────────────────────────────────

def test_reparse_unknown_source_is_404(client):
    assert client.post(f"/api/kb/sources/{uuid.uuid4()}/reparse").status_code == 404


def test_reparse_without_retained_file_is_409(client, files_dir):
    store = get_source_store()
    sid = store.create(
        filename="https://example.org/a", title="a", source_kind="web",
        full_text="some text", content_hash=uuid.uuid4().hex, origin_url=f"https://example.org/{uuid.uuid4().hex}",
    )
    r = client.post(f"/api/kb/sources/{sid}/reparse")
    assert r.status_code == 409 and "原始文件" in r.json()["detail"]


def test_reparse_rebuilds_chunks_from_the_retained_file(client, files_dir):
    from app.db.chunk_store import get_chunk_store

    tag = uuid.uuid4().hex
    result = _upload(client, _text(tag))
    sid = result["source_id"]
    original = next(p for p in files_dir.iterdir() if p.stem == sid)
    new_tag = uuid.uuid4().hex
    original.write_text(
        _text(new_tag) + " Reparsed with the current profile: polyurethane topcoat gloss 90 GU.",
        encoding="utf-8",
    )

    r = client.post(f"/api/kb/sources/{sid}/reparse")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["task_id"] and body["stream_url"].endswith("/stream")
    task = _wait(client, body["task_id"])
    assert task.get("state") == "completed", task
    assert task["result"]["source_id"] == sid and task["result"]["chunks"] >= 1

    chunks = " ".join(c.text for c in get_chunk_store().get_by_source(sid))
    assert new_tag in chunks and tag not in chunks
    row = get_source_store().get(sid)
    assert row.raw_text_chars == task["result"]["chars"] and row.ingest_status == "indexed"


def test_reparse_failure_is_reported_on_the_task(client, files_dir, monkeypatch):
    result = _upload(client, _text(uuid.uuid4().hex))
    sid = result["source_id"]

    def boom(*a, **k):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr("app.services.kb_reparse._parse", boom)
    r = client.post(f"/api/kb/sources/{sid}/reparse")
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    task = _wait(client, task_id)
    assert task.get("state") == "failed"
    # The failure message is written in two steps (persist_result, then
    # _persist_terminal); a poll landing between them used to see the generic
    # "failed". Both steps now carry the exception text, but keep waiting for
    # the final text so any residual scheduling window cannot flake the test.
    deadline = time.monotonic() + 10
    while "parser exploded" not in (task.get("message") or str(task.get("result"))):
        if time.monotonic() > deadline:
            break
        time.sleep(0.1)
        task = client.get(f"/api/tasks/{task_id}").json()
    assert "parser exploded" in (task.get("message") or str(task.get("result"))), task


# ── 3. POST /api/kb/sources/resolve ─────────────────────────────────────────

def test_resolve_maps_patent_aliases_doi_and_url():
    store = get_source_store()
    n = str(uuid.uuid4().int)[:9]
    pat = store.create(filename="p", title="Patent", source_kind="patent", full_text="x",
                       content_hash=uuid.uuid4().hex, origin_url=f"CN{n}B")
    doi = f"10.1234/abc.{uuid.uuid4().hex[:6]}"
    paper = store.create(filename="d", title="Paper", source_kind="paper", full_text="x",
                         content_hash=uuid.uuid4().hex, origin_url=doi)
    url = f"https://example.org/{uuid.uuid4().hex}"
    web = store.create(filename="w", title="Web", source_kind="web", full_text="x",
                       content_hash=uuid.uuid4().hex, origin_url=url)
    r = TestClient(app).post("/api/kb/sources/resolve", json={"items": [
        {"identifier": f"CN-{n}-B"},
        {"identifier": doi},
        {"identifier": "some-search-id", "url": url},
        {"identifier": "missing-id-xyz"},
    ]})
    assert r.status_code == 200, r.text
    got = {m["identifier"]: m["source_id"] for m in r.json()["matches"]}
    assert got == {f"CN-{n}-B": pat, doi: paper, "some-search-id": web}


def test_resolve_ignores_failed_rows_and_validates_size():
    store = get_source_store()
    origin = f"https://example.org/failed-{uuid.uuid4().hex}"
    store.record_ingest_failure(origin_url=origin, filename="f", title="f", source_kind="web",
                                project_id=None, error="boom")
    c = TestClient(app)
    assert c.post("/api/kb/sources/resolve", json={"items": [{"identifier": origin}]}).json()["matches"] == []
    assert c.post("/api/kb/sources/resolve", json={"items": [{"identifier": "x"}] * 501}).status_code == 422
