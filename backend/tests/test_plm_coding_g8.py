"""G8 generic product coding: rules validation, auto-coding, frozen codes."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.services.plm import company as company_svc
from app.services.plm.coding import (
    empty_rules,
    generate_product_code,
    next_code,
    normalize_rules,
    validate_coding_rules,
)
from app.services.plm.documents import service as docs

RULES_3 = {"categories": [{"name_zh": "磷化剂", "name_en": "Phosphating", "identifier": "4"},
                          {"name_zh": "表面活性剂", "name_en": "Surfactant", "identifier": "3"}],
           "code_digits": 3}


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/g8.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def _default_company(s, rules):
    return company_svc.create_company(s, {"name": "测试公司", "emergency_phone": "010-12345678",
                                          "is_default": True, "coding_rules": rules})


# --------------------------------------------------------------------------- validation


def test_validate_empty_and_normalization():
    assert validate_coding_rules(None) == empty_rules()
    assert validate_coding_rules({}) == empty_rules()
    assert normalize_rules(None) == empty_rules()
    assert normalize_rules({"categories": [], "code_digits": None}) == empty_rules()
    assert validate_coding_rules(RULES_3) == RULES_3


def test_validate_rejects_bad_rules():
    with pytest.raises(ValueError, match="重复"):
        validate_coding_rules({"categories": [{"name_zh": "A", "identifier": "4"},
                                               {"name_zh": "B", "identifier": "4"}], "code_digits": 3})
    with pytest.raises(ValueError, match="识别码"):
        validate_coding_rules({"categories": [{"name_zh": "A", "identifier": "4-1"}], "code_digits": 3})
    with pytest.raises(ValueError, match="中文名称"):
        validate_coding_rules({"categories": [{"name_zh": "", "identifier": "4"}], "code_digits": 3})
    with pytest.raises(ValueError, match="必须为整数"):
        validate_coding_rules({"categories": [{"name_zh": "A", "identifier": "4"}],
                               "code_digits": None})
    with pytest.raises(ValueError, match="无意义"):
        validate_coding_rules({"categories": [], "code_digits": 3})
    with pytest.raises(ValueError, match="大于最长识别码"):
        validate_coding_rules({"categories": [{"name_zh": "A", "identifier": "AB"}], "code_digits": 2})


# --------------------------------------------------------------------------- auto-coding


def test_auto_code_sequence_and_serial_scan(sf):
    with sf() as s:
        _default_company(s, RULES_3)
        p1 = docs.create_product(s, name_zh="磷化剂 A", category_identifier="4")
        p2 = docs.create_product(s, name_zh="磷化剂 B", category_identifier="4")
        p3 = docs.create_product(s, name_zh="表活 A", category_identifier="3")
        assert (p1.code, p2.code, p3.code) == ("401", "402", "301")
        assert p1.category_identifier == "4" and p3.category_identifier == "3"


def test_digit_change_only_affects_new_products(sf):
    with sf() as s:
        company = _default_company(s, RULES_3)
        p1 = docs.create_product(s, name_zh="磷化剂 A", category_identifier="4")
        p2 = docs.create_product(s, name_zh="磷化剂 B", category_identifier="4")
        company_svc.update_company(s, company.id, {"coding_rules": {**RULES_3, "code_digits": 4}})
        p3 = docs.create_product(s, name_zh="磷化剂 C", category_identifier="4")
        assert p3.code == "4003"  # serial continues (1, 2 used), new length applies
        s.refresh(p1)
        s.refresh(p2)
        assert (p1.code, p2.code) == ("401", "402")  # frozen, not rewritten


def test_auto_code_errors(sf):
    with sf() as s:
        _default_company(s, RULES_3)
        with pytest.raises(docs.DocumentError, match="二选一"):
            docs.create_product(s, code="MANUAL", name_zh="冲突", category_identifier="4")
        with pytest.raises(docs.DocumentError, match="不在默认公司的编码规则中"):
            docs.create_product(s, name_zh="未知", category_identifier="9")


def test_auto_code_without_rules_rejected(sf):
    with sf() as s:
        company_svc.create_company(s, {"name": "无规则公司", "emergency_phone": "010-12345678",
                                       "is_default": True})
        with pytest.raises(docs.DocumentError, match="未配置产品编码规则"):
            docs.create_product(s, name_zh="无规则", category_identifier="4")


def test_manual_code_and_legacy_code_still_work(sf):
    with sf() as s:
        _default_company(s, RULES_3)
        p = docs.create_product(s, code="MAN-001", name_zh="手工产品", legacy_code="MPV 401")
        assert p.code == "MAN-001" and p.category_identifier is None and p.legacy_code == "MPV 401"
        with pytest.raises(docs.DocumentError, match="已存在"):
            docs.create_product(s, code="MAN-001", name_zh="重复")
        with pytest.raises(docs.DocumentError, match="不能为空"):
            docs.create_product(s, name_zh="无码无类")


def test_revision_shares_product_code(sf):
    with sf() as s:
        _default_company(s, RULES_3)
        p = docs.create_product(s, name_zh="磷化剂 A", category_identifier="4")
        docs.create_revision(s, p.id)
        docs.create_revision(s, p.id)
        s.refresh(p)
        assert p.code == "401"


def test_next_code_overflow(sf):
    with sf() as s:
        _default_company(s, {"categories": [{"name_zh": "磷化剂", "identifier": "4"}],
                             "code_digits": 2})
        docs.create_product(s, name_zh="满", category_identifier="4")  # 41 .. 49
        for _ in range(8):
            docs.create_product(s, name_zh="满", category_identifier="4")
        with pytest.raises(docs.DocumentError, match="位数已用完"):
            docs.create_product(s, name_zh="溢出", category_identifier="4")
        with pytest.raises(ValueError, match="位数已用完"):
            next_code(s, "4", 2)


def test_generate_code_rejects_unknown_identifier(sf):
    with sf() as s:
        _default_company(s, RULES_3)
        with pytest.raises(ValueError, match="不在默认公司的编码规则中"):
            generate_product_code(s, RULES_3, "9")
