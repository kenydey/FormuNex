"""Batch 7 (P3 backlog) directed tests — chunking, vision, llm, GHS, ELN, history.

Each test pins one batch-7 behaviour change; items verified only by probe
or code inspection (A9补位 concurrency, B30 log tripwire, C6 flash-point
warning path, C20 PDF bytes) are listed in the batch-7 report, not here.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

# ── B28 / B29 / B31 / B34 / B38 / B39 / B40: chunking ────────────────────────

from app.services.chunking import (
    _split_blocks,
    _split_sections,
    _truncate_heading_path,
    chunk_markdown,
    chunk_plain_text,
    mark_example_boundaries,
)


def test_b28_heading_path_cuts_at_level_boundary():
    assert _truncate_heading_path("A" * 50 + " > " + "B" * 50) == "A" * 50
    assert len(_truncate_heading_path("X" * 100)) == 80  # single title: hard cut


def test_b29_empty_title_does_not_open_a_section():
    secs = _split_sections("# A\n\n## \n\ntext\n\n## B\n\nmore")
    assert [p for p, _ in secs] == ["A", "A > B"]
    assert secs[0][1].startswith("##")


def test_b31_abbreviation_and_decimal_guards():
    base = "甲" * 100 + " e.g. test " + "乙" * 100
    # Guarded ". " must not split: falls to hard slices, not [105, 105].
    assert [len(c) for c in chunk_plain_text(base, max_chars=150, overlap=0)] == [150, 61]
    fig = "甲" * 100 + " Fig. 3 shows " + "乙" * 100
    assert [len(c) for c in chunk_plain_text(fig, max_chars=150, overlap=0)] == [150, 64]
    dec = "甲" * 100 + " 3. 5 mg " + "乙" * 100  # " 3. 5 mg " is 9 chars -> 209 total
    assert [len(c) for c in chunk_plain_text(dec, max_chars=150, overlap=0)] == [150, 59]
    fw = "甲" * 100 + "．" + "乙" * 100 + " 3．5 mg " + "丙" * 100
    assert [len(c) for c in chunk_plain_text(fw, max_chars=150, overlap=0)] == [101, 150, 58]
    plain = "甲" * 100 + ". test " + "乙" * 100
    assert [len(c) for c in chunk_plain_text(plain, max_chars=150, overlap=0)] == [101, 105]


def test_b34_table_tolerates_exactly_one_blank_line():
    one_blank = "| a | b |\n|---|---|\n| 1 | 2 |\n\n| 3 | 4 |"
    assert len(_split_blocks(one_blank)) == 1
    two_blanks = "| a | b |\n|---|---|\n| 1 | 2 |\n\n\n| 3 | 4 |"
    assert len(_split_blocks(two_blanks)) == 2


def test_b38_page_end_reachable_cases():
    boundary = chunk_markdown("# T\n\npara one here\n\n<!-- page:2 -->")
    assert [(c.page_no, c.page_end) for c in boundary] == [(None, 2)]
    caption_pair = chunk_markdown(
        "<!-- page:1 -->\n\n表 1 规模\n\n<!-- page:2 -->\n\n| a |\n|---|\n| 1 |"
    )
    assert [(c.page_no, c.page_end) for c in caption_pair] == [(1, 2)]


def test_b39_example_patterns():
    md = "实施例三\n\nA\n\n实施例 3-1\n\nB\n\nEmbodiments\n\nC\n\n第 实施例 2\n\nD"
    heads = [line for line in mark_example_boundaries(md).split("\n") if line.startswith("##")]
    assert heads == ["## 实施例三", "## 实施例 3-1", "## Embodiments", "## 第 实施例 2"]


def test_b40_hard_cut_segments_share_paragraph_idx():
    chunks = chunk_markdown("# T\n\n" + "word " * 500, max_chars=200, overlap=0)
    assert len(chunks) > 1
    assert {c.paragraph_idx for c in chunks} == {0}


# ── B1: footnote single-pass expansion ───────────────────────────────────────

from app.services.vision_extract import (
    VisionFootnote,
    VisionReaction,
    VisionReactionCondition,
    merge_footnotes,
)


def test_b1_expansion_result_is_not_reexpanded():
    rxn = VisionReaction(
        entry="E1",
        conditions=[VisionReactionCondition(text="in THF at RT")],
    )
    footnotes = [
        VisionFootnote(key="f1", text="THF = tetrahydrofuran RT"),
        VisionFootnote(key="f2", text="RT = room temperature"),
    ]
    out = merge_footnotes([rxn], footnotes)
    text = out[0].conditions[0].text
    assert "tetrahydrofuran RT" in text  # RT inside the THF expansion survives
    assert text.count("room temperature") == 1  # only the original RT expands


# ── B2: canonical SMILES dedup ───────────────────────────────────────────────

from app.services.chem_extract import extract_smiles


def test_b2_same_canonical_from_different_raw_kept_once():
    try:
        import rdkit  # noqa: F401
    except Exception:
        pytest.skip("RDKit unavailable")
    # Tokens under 5 chars are ignored by extract_smiles, so use two
    # longer spellings of acetic acid (same canonical form).
    got = extract_smiles("dissolve CC(=O)O then CC(O)=O and stir")
    assert len(got) == 1
    assert got[0]["raw"] == "CC(=O)O" and got[0]["canonical"] == "CC(=O)O"


# ── B4: structured-unsupported TTL ───────────────────────────────────────────

import app.services.llm as llm_mod


def test_b4_structured_unsupported_expires():
    llm_mod._STRUCTURED_UNSUPPORTED.clear()
    try:
        llm_mod._mark_structured_unsupported("prov", "model-x")
        assert llm_mod._structured_unsupported("prov", "model-x") is True
        assert llm_mod._structured_unsupported("prov", "other-model") is False
        llm_mod._STRUCTURED_UNSUPPORTED[("prov", "model-x")] = (
            time.monotonic() - llm_mod._STRUCTURED_UNSUPPORTED_TTL_S - 1
        )
        assert llm_mod._structured_unsupported("prov", "model-x") is False
        assert ("prov", "model-x") not in llm_mod._STRUCTURED_UNSUPPORTED
    finally:
        llm_mod._STRUCTURED_UNSUPPORTED.clear()


# ── B24: run_agents duplicate names ──────────────────────────────────────────

from app.services.vision_plan import run_agents


def test_b24_duplicate_agent_names_fail_fast():
    with pytest.raises(ValueError, match="duplicate agent names"):
        run_agents([("a", lambda: 1), ("a", lambda: 2)])
    results, failed = run_agents([("a", lambda: 1), ("b", lambda: 2)])
    assert results == {"a": 1, "b": 2} and failed == []


# ── B27: unbalanced-paren report ─────────────────────────────────────────────

from app.services.chem_split import split_top_level


def test_b27_unbalanced_parens_reported_not_silently_clamped():
    report: list = []
    assert split_top_level("A), B", report=report) == ["A)", "B"]
    assert report and report[0]["reason"] == "unbalanced_parens"
    assert report[0]["stray_closers"] == 1
    report2: list = []
    assert split_top_level("A (2 equiv), B", report=report2) == ["A (2 equiv)", "B"]
    assert report2 == []


# ── A4: unknown-operation rows expire ────────────────────────────────────────

from app.db.database import Base, make_engine, make_session_factory
from app.db.dispatcher import _expire_ancient
from app.db.models import TaskOutbox


def test_a4_unknown_operation_rows_expire(tmp_path):
    # make_engine already runs create_all_metadata in non-prod envs,
    # so the table exists; no explicit create (which errors "already
    # exists"). A file DB keeps this test isolated from other tests.
    engine = make_engine(f"sqlite:///{tmp_path}/outbox.db")
    factory = make_session_factory(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with factory() as session:
        session.add(TaskOutbox(
            id="old-unknown", operation="op_no_longer_exists", idempotency_key="k1",
            payload={}, status="PENDING",
            created_at=now - timedelta(days=10), updated_at=now - timedelta(days=10),
        ))
        session.add(TaskOutbox(
            id="fresh-unknown", operation="op_no_longer_exists", idempotency_key="k2",
            payload={}, status="PENDING",
            created_at=now, updated_at=now,
        ))
        session.commit()
        n = _expire_ancient(session, now - timedelta(days=1))
        assert n == 1
        assert session.get(TaskOutbox, "old-unknown").status == "DEAD"
        assert session.get(TaskOutbox, "fresh-unknown").status == "PENDING"


# ── A8: progress disk mirror on Redis success ────────────────────────────────

import app.worker.task_progress as tp_mod
from app.worker.task_progress import TaskProgressEvent, TaskProgressStatus, _store_progress


class _FakeRedis:
    def __init__(self):
        self.published = []

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def hset(self, *args, **kwargs):
        return 1

    def expire(self, *args, **kwargs):
        return True


def test_a8_success_path_also_mirrors_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_TASK_PROGRESS_DIR", str(tmp_path))
    fake = _FakeRedis()
    monkeypatch.setattr(tp_mod, "_redis_client", lambda: fake)
    _store_progress(
        "task-a8",
        TaskProgressEvent(status=TaskProgressStatus.RUNNING, stage="mix", message="hi"),
    )
    assert fake.published, "Redis publish must still happen"
    meta_path = tmp_path / "task-a8.meta.json"
    assert meta_path.exists()
    assert '"RUNNING"' in meta_path.read_text(encoding="utf-8")


# ── C8: generic eye "2" preserved ────────────────────────────────────────────

from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties


def _cats(result, hazard_class):
    return [o.category for o in result.hazards if o.hazard_class == hazard_class]


def test_c8_generic_eye_2_stays_2_not_upgraded_to_2a():
    eye2 = ComponentHazard("eye_damage", "2", "H319")
    result = classify(
        [Component("A", 50, hazards=[eye2]), Component("水", 50, hazards=[])],
        MixtureProperties(),
    )
    assert _cats(result, "eye_damage") == ["2"]


def test_c8_eye_2b_and_mixed_still_behave():
    eye2b = ComponentHazard("eye_damage", "2B", "H319")
    result = classify(
        [Component("A", 50, hazards=[eye2b]), Component("水", 50, hazards=[])],
        MixtureProperties(),
    )
    assert _cats(result, "eye_damage") == ["2B"]
    eye2 = ComponentHazard("eye_damage", "2", "H319")
    mixed = classify(
        [Component("A", 30, hazards=[eye2]), Component("B", 30, hazards=[eye2b]),
         Component("水", 40, hazards=[])],
        MixtureProperties(),
    )
    assert _cats(mixed, "eye_damage") == ["2A"]


# ── C13 / C14: cache deepcopy, digest normalization ──────────────────────────

from app.services.chem_common import cache_get, cache_put
from app.services.eln.sync import _digest


def test_c13_cache_get_returns_copy():
    store: dict = {}
    cache_put(store, "k", {"cas": "1-2-3", "names": ["a"]})
    first = cache_get(store, "k", 3600)
    first["cas"] = "MUTATED"
    first["names"].append("b")
    second = cache_get(store, "k", 3600)
    assert second == {"cas": "1-2-3", "names": ["a"]}


def test_c14_digest_int_float_equal():
    assert _digest({"m": 500}) == _digest({"m": 500.0})
    assert _digest({"m": 500}) != _digest({"m": 501})


# ── C17/C18: save_version ────────────────────────────────────────────────────

import app.services.formulation_history as history_mod
from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.formulation_history import FormulationHistoryStore


def _form(**weights):
    weights = weights or {"Epoxy": 60.0, "Talc": 40.0}
    return Formulation(
        name="T",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name=n, role="resin" if n == "Epoxy" else "filler", weight_pct=v)
            for n, v in weights.items()
        ],
        predicted={"salt_spray_hours": 800.0, "cost_cny_per_kg": 20.0},
    )


@pytest.fixture()
def hist_store(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/history.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    st = FormulationHistoryStore(factory)
    monkeypatch.setattr(history_mod, "_store", st)
    return st


def test_c18_unknown_parent_raises_instead_of_new_lineage(hist_store):
    with pytest.raises(ValueError, match="父版本"):
        hist_store.save_version(_form(), parent_version_id="no-such-id")


def test_c17_c18_normal_save_still_works(hist_store):
    v1 = hist_store.save_version(_form(), created_by="a")
    v2 = hist_store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=v1.id)
    assert (v1.version, v2.version) == (1, 2)
    assert v2.lineage_id == v1.lineage_id


# ── C19: _y_best skips missing ────────────────────────────────────────────────

from app.domain.schemas import ExperimentRecord
from app.services.doe_explain import _y_best


def _rec(measured):
    # Normal construction: the _lift_legacy_measured validator turns
    # measured= into typed measurement rows; model_construct would bypass
    # it and leave the computed `measured` property empty.
    return ExperimentRecord(domain=ProductDomain.anticorrosion_coating, measured=measured)


def test_c19_y_best_skips_records_missing_the_metric():
    recs = [_rec({}), _rec({"yield": 300.0}), _rec({"yield": 280.0})]
    assert _y_best(recs, "yield", "minimize") == 280.0
    assert _y_best(recs, "yield", "maximize") == 300.0
    assert _y_best([_rec({})], "yield", "minimize") == 0.0
