"""``ChatRequest.ref_doc_ids`` (@-referenced KB documents) restricts the KB half of chat retrieval."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.db.source_store import get_source_store
from app.domain.schemas import Evidence
from app.main import app
from app.services.kb_index import index_source


def _doc(text: str) -> str:
    sid = get_source_store().create(
        filename="doc.txt", title="Doc", source_kind="local", full_text=text, content_hash=uuid.uuid4().hex,
    )
    assert index_source(sid, text, embed=False) >= 1
    return sid


def _ev(identifier: str, source: str = "kb") -> Evidence:
    return Evidence(source=source, identifier=identifier, title=identifier, snippet="snippet text", relevance=0.5)


@pytest.fixture()
def two_docs():
    tag = uuid.uuid4().hex[:8]
    a = _doc(f"Waterborne epoxy zinc primer {tag}: salt spray resistance 1000 hours with 80 percent zinc dust loading.")
    b = _doc(f"Polyurethane topcoat {tag}: gloss retention after QUV 2000 hours, aliphatic isocyanate HDI trimer.")
    return a, b


def test_no_refs_leaves_sources_untouched():
    src = [_ev("kb:x#c0"), _ev("u1", "patent")]
    assert chat_api._apply_ref_docs("q", src, [], k=6) == (src, 0)
    assert chat_api._apply_ref_docs("q", src, None, k=6) == (src, 0)


def test_refs_go_first_and_other_kb_chunks_are_dropped(two_docs):
    a, b = two_docs
    sources = [_ev(f"kb:{b}#c0"), _ev("CN123456B", "patent"), _ev("wiki:page-1", "wiki")]
    out, added = chat_api._apply_ref_docs("zinc primer salt spray", sources, [a], k=6)
    ids = [e.identifier for e in out]
    assert added >= 1 and ids[0].startswith(f"kb:{a}#c")
    assert not any(i.startswith(f"kb:{b}") for i in ids), "KB chunks of unreferenced documents are dropped"
    assert "CN123456B" in ids and "wiki:page-1" in ids, "non-KB evidence is kept"


def test_every_referenced_document_contributes(two_docs):
    a, b = two_docs
    out, added = chat_api._apply_ref_docs("zinc primer salt spray", [], [a, b], k=6)
    srcs = {chat_api._kb_source_of(e) for e in out}
    assert {a, b} <= srcs and added == len(out)


def test_ref_chunks_already_retrieved_are_not_duplicated(two_docs):
    a, _b = two_docs
    out, _ = chat_api._apply_ref_docs("zinc", [_ev(f"kb:{a}#c0")], [a], k=6)
    assert [e.identifier for e in out].count(f"kb:{a}#c0") == 1


def test_unknown_refs_and_errors_fail_open(monkeypatch):
    src = [_ev("kb:other#c0")]
    assert chat_api._apply_ref_docs("q", src, [str(uuid.uuid4())], k=6) == (src, 0)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.chunk_store.get_chunk_store", boom)
    assert chat_api._apply_ref_docs("q", src, ["abc"], k=6) == (src, 0)


@pytest.fixture()
def spy(monkeypatch):
    calls: list[list[str]] = []
    real = chat_api._apply_ref_docs

    def _spy(question, sources, ref_doc_ids, *, k):
        calls.append(list(ref_doc_ids or []))
        return real(question, sources, ref_doc_ids, k=k)

    monkeypatch.setattr(chat_api, "_apply_ref_docs", _spy)
    return calls


BODY = {
    "question": "环氧锌粉底漆的耐盐雾性能？",
    "sources": [{"source": "seed", "identifier": "s1", "title": "t", "snippet": "环氧锌粉底漆 耐盐雾 1000 h", "relevance": 0.9}],
}


def test_chat_endpoint_applies_ref_doc_ids(spy, two_docs):
    a, _ = two_docs
    r = TestClient(app).post("/api/chat", json={**BODY, "ref_doc_ids": [a]})
    assert r.status_code == 200, r.text
    assert [a] in spy


def test_chat_stream_applies_ref_doc_ids(spy, two_docs):
    a, _ = two_docs
    r = TestClient(app).post("/api/chat/stream", json={**BODY, "ref_doc_ids": [a]})
    assert r.status_code == 200, r.text
    events = [json.loads(b.strip()[6:]) for b in r.text.split("\n\n") if b.strip().startswith("data: ")]
    assert events, r.text
    assert [a] in spy


def test_stream_answer_plan_applies_ref_doc_ids(spy, two_docs):
    """The keyed streaming path prepares retrieval in ``_stream_answer_plan`` (not ``chat``)."""
    from app.config import get_settings

    a, _ = two_docs
    req = chat_api.ChatRequestValidated(**BODY, ref_doc_ids=[a])
    try:
        chat_api._stream_answer_plan(req, get_settings())
    except Exception:  # noqa: BLE001 - later stages may need an LLM; retrieval ran first
        pass
    assert [a] in spy
