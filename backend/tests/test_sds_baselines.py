"""SDS manual-review baselines (plan 1-9): 10 baseline frameworks, 5 zh + 5 en.

Each baseline is a full 16-section SDS generated from an example mixture;
the key fields (classification, H/P statements, pictograms, signal word)
are explicitly marked pending EHS / quality review. The test asserts the
generated SDS matches the baseline structure — it does NOT assert the
pending fields are approved.

Status: PENDING EHS / QUALITY REVIEW. Until the sign-off sheet
(``docs/ehs-signoff-gb30000.md``) is signed, these are frameworks, not
approved baselines.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.plm.documents.sds_builder import SECTION_TITLES, SdsContext, build_sds
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties

DATA = Path(__file__).resolve().parent / "golden" / "sds_baselines.yaml"
COMPANY = {"name": "方枢示例化工有限公司", "address": "上海市示例路 1 号",
           "phone": "021-0000", "emergency_phone": "021-0001"}
PRODUCT = {"name_zh": "FX-基线示例产品", "name_en": "FX-Baseline Example", "code": "FX-BASE"}


def _load():
    with DATA.open(encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    baselines = payload["baselines"]
    ids = [b["id"] for b in baselines]
    assert len(ids) == len(set(ids)), "duplicate SDS baseline ids"
    return baselines


BASELINES = _load()
SECTION_KEYS = [k for k, _, _ in SECTION_TITLES]


def test_baseline_set_covers_both_languages():
    langs = [b["lang"] for b in BASELINES]
    assert len(BASELINES) == 10
    assert langs.count("zh-CN") == 5 and langs.count("en-US") == 5
    assert all(b["hazard_profile"] for b in BASELINES)


def test_every_baseline_is_explicitly_pending_review():
    for b in BASELINES:
        assert b.get("pending_ehs_review") is True, f"{b['id']} must be marked pending EHS review"
        pending = b.get("pending_fields") or []
        assert pending, f"{b['id']} must list pending fields"
        assert any("分类" in f or "classification" in f for f in pending), f"{b['id']} must list classification as pending"


@pytest.mark.parametrize("baseline", BASELINES, ids=[b["id"] for b in BASELINES])
def test_generated_sds_matches_baseline_structure(baseline):
    comps = [Component(c["name"], float(c["pct"]), c.get("cas"),
                        hazards=[ComponentHazard(h["hazard_class"], str(h["category"]), h.get("h_code"))
                                 for h in c.get("hazards", [])])
             for c in baseline["components"]]
    assert abs(sum(c.pct for c in comps) - 100.0) < 0.01, f"{baseline['id']}: components must sum to 100"
    props = MixtureProperties(**{k: v for k, v in (baseline.get("props") or {}).items()
                                 if k in MixtureProperties.__dataclass_fields__})
    cls = classify(comps, props).to_dict()
    cls["statements"] = {c: {"zh": f"{c}中文", "en": ""} for c in cls["h_codes"] + cls["p_codes"]}
    ctx = SdsContext(product=PRODUCT, company=COMPANY, classification=cls,
                     statements=cls["statements"], components=cls["disclosure"],
                     revision={"version": "A.1", "date": "2026-10-18"}, lang=baseline["lang"])
    sds = build_sds(ctx)
    # 16 sections, GB/T 16483 order.
    assert [s["no"] for s in sds["sections"]] == list(range(1, 17))
    assert [s["key"] for s in sds["sections"]] == SECTION_KEYS
    assert sds["title"] == ("化学品安全技术说明书" if baseline["lang"] == "zh-CN" else "Safety Data Sheet")
    # Section 2 carries the pending-review key fields.
    hz = {f["key"]: f for f in sds["sections"][1]["fields"]}
    assert {"pictograms", "signal_word", "hazard_statements"} <= set(hz)
    # The baseline's hazard profile is reflected in the generated classification.
    got_classes = {h["hazard_class"] for h in cls["hazards"]}
    assert set(baseline["hazard_profile"]) <= got_classes, (
        f"{baseline['id']}: profile {baseline['hazard_profile']} not in generated {sorted(got_classes)}")
