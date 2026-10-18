"""Plan 1-1: GB 30000 rule-set EHS sign-off mechanism.

The rule-set file stays ``status: draft`` until an EHS specialist reviews it;
the gate blocks released documents from citing classification results computed
with a rule set that is not (a) marked ``released`` AND (b) backed by a valid
sign-off record. These tests cover the mechanism itself — the actual EHS
signature (flipping the status + filing the record) is still pending.
"""
from __future__ import annotations

import pytest

from app.services.plm.ghs.ruleset import RuleSet, RuleSetSignoff, load_ruleset, load_signoff
from app.services.plm.release.gate import Check, check_ruleset_signoff

VALID = {"signer": "张三（EHS 法规专员）", "signed_at": "2026-10-17",
         "version": "gb30000-2013.v1", "decision": "approved", "comment": "逐条核对，与 GB 30000 一致。"}


def _signoff(**over):
    data = {**VALID, **over}
    return RuleSetSignoff(**data)


# --------------------------------------------------------------------------- record validation


def test_load_ruleset_rejects_hostile_names():
    # Overlong names raise OSError (ENAMETOOLONG) from is_file(), not
    # FileNotFoundError; traversal must not escape the ruleset dir.
    with pytest.raises(FileNotFoundError):
        load_ruleset("a" * 3000)
    with pytest.raises(FileNotFoundError):
        load_ruleset("../etc/passwd")
    with pytest.raises(FileNotFoundError):
        load_ruleset("")
    assert load_signoff("a" * 3000) is None


def test_valid_signoff_has_no_problems():
    assert _signoff().problems("gb30000-2013.v1") == []


def test_signoff_problems_cover_signer_date_version_decision():
    assert any("签核人" in p for p in _signoff(signer="").problems("gb30000-2013.v1"))
    assert any("签核日期" in p for p in _signoff(signed_at="").problems("gb30000-2013.v1"))
    assert any("不是合法" in p for p in _signoff(signed_at="2026-13-40").problems("gb30000-2013.v1"))
    assert any("未来" in p for p in _signoff(signed_at="2099-01-01").problems("gb30000-2013.v1"))
    assert any("不符" in p for p in _signoff(version="gb30000-2013.v2").problems("gb30000-2013.v1"))
    assert any("approved" in p for p in _signoff(decision="rejected").problems("gb30000-2013.v1"))


def test_load_signoff_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.plm.ghs.ruleset.SIGNOFF_DIR", tmp_path)
    assert load_signoff("gb30000-2013.v1") is None


def test_load_signoff_parses_yaml_file(monkeypatch, tmp_path):
    (tmp_path / "gb30000-2013.v1.yaml").write_text(
        "signer: 李四\nsigned_at: 2026-10-17\nversion: gb30000-2013.v1\ndecision: approved\ncomment: 已复核。\n",
        encoding="utf-8")
    monkeypatch.setattr("app.services.plm.ghs.ruleset.SIGNOFF_DIR", tmp_path)
    rec = load_signoff("gb30000-2013.v1")
    assert rec is not None and rec.signer == "李四" and rec.problems("gb30000-2013.v1") == []


# --------------------------------------------------------------------------- gate enforcement


def _check(rs):
    c = Check("classification_current", "G5", "分类结果基于当前规则集与法规数据")
    check_ruleset_signoff(c, rs)
    return c


def test_gate_blocks_draft_ruleset_with_signoff_message():
    rs = load_ruleset()  # repo rule set is still draft — EHS sign-off pending
    assert rs.status == "draft"
    c = _check(rs)
    assert any("待 EHS 签核" in i["message"] for i in c.items)
    assert c.evidence["rule_set_status"] == "draft"


def test_gate_blocks_released_ruleset_without_signoff_record(monkeypatch):
    data = load_ruleset().as_dict()
    data["status"] = "released"
    monkeypatch.setattr("app.services.plm.release.gate.load_signoff", lambda version: None)
    c = _check(RuleSet(data))
    assert any("缺少 EHS 签核记录" in i["message"] for i in c.items)


def test_gate_blocks_released_ruleset_with_invalid_signoff(monkeypatch):
    data = load_ruleset().as_dict()
    data["status"] = "released"
    monkeypatch.setattr("app.services.plm.release.gate.load_signoff",
                        lambda version: _signoff(version="gb30000-2013.v2"))
    c = _check(RuleSet(data))
    assert any("不符" in i["message"] for i in c.items)


def test_gate_passes_released_ruleset_with_valid_signoff(monkeypatch):
    data = load_ruleset().as_dict()
    data["status"] = "released"
    monkeypatch.setattr("app.services.plm.release.gate.load_signoff",
                        lambda version: _signoff(version=version))
    c = _check(RuleSet(data))
    assert c.items == []
    assert c.evidence["rule_set_signoff"]["signer"] == "张三（EHS 法规专员）"
