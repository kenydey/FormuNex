"""《危险化学品目录》 free-text hazard parser — cases taken verbatim from the catalogue."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.plm.regulatory.catalog_text_parser import parse_hazard_text

CATALOGUE = Path(__file__).resolve().parents[1] / "app" / "resources" / "regulatory" / "hazard_chemical_info.json"


def _items(text):
    r = parse_hazard_text(text)
    return [(i.hazard_class, i.category, i.h_code, i.is_minimum) for i in r.items], r


def test_run_together_classes_with_minimum_markers():
    items, r = _items("急性毒性-经口,类别 3*急性毒性-吸入,类别 3*致癌性,类别 1A")
    assert items == [
        ("acute_tox_oral", "3", "H301", True),
        ("acute_tox_inhal", "3", "H331", True),
        ("carcinogenicity", "1A", "H350", False),
    ]
    assert not r.needs_review


def test_stot_se3_effect_on_following_line():
    r = parse_hazard_text("特异性靶器官毒性-一次接触,类别 3\n（呼吸道刺激）\n危害水生环境-急性危害,类别 1")
    assert [(i.hazard_class, i.h_code, i.qualifier) for i in r.items] == [
        ("stot_se", "H335", "（呼吸道刺激）"),
        ("aquatic_acute", "H400", ""),
    ]
    assert not r.needs_review


def test_stot_se3_both_effects():
    r = parse_hazard_text("特异性靶器官毒性- 一次接触, 类别 3(呼吸道刺激、麻醉效应)")
    assert [(i.category, i.h_code) for i in r.items] == [("3", "H335,H336")]


def test_stot_se3_without_effect_goes_to_review():
    r = parse_hazard_text("特异性靶器官毒性-一次接触,类别 3")
    assert r.items[0].h_code is None
    assert "stot_se3_effect_missing" in r.review_reasons


def test_class_name_broken_across_lines():
    items, r = _items("遇水放出易燃气体的物质和混合物,\n类别 2")
    assert items == [("water_reactive", "2", "H261", False)]


def test_conditional_classification_keeps_conditions_and_needs_review():
    r = parse_hazard_text("（1）闪点＜23℃和初沸点≤35℃：易燃液体,类别 1\n（2）闪点＜23℃和初沸点＞35℃：易燃液体，类别 2")
    assert [(i.category, i.condition) for i in r.items] == [
        ("1", "闪点＜23℃和初沸点≤35℃"),
        ("2", "闪点＜23℃和初沸点＞35℃"),
    ]
    assert r.conditional and "conditional" in r.review_reasons


def test_types_divisions_and_special_categories():
    assert _items("有机过氧化物 F 型")[0] == [("organic_peroxide", "F", "H242", False)]
    assert _items("爆炸物，不稳定爆炸物")[0] == [("explosive", "unstable", "H200", False)]
    assert _items("爆炸物,1.1 项")[0] == [("explosive", "1.1", "H201", False)]
    assert _items("生殖毒性,类别 1B生殖毒性,附加类别")[0] == [
        ("reproductive", "1B", "H360", False),
        ("reproductive", "lact", "H362", False),
    ]
    assert _items("易燃气体,类别 1加压气体")[0] == [
        ("flammable_gas", "1", "H220", False),
        ("gas_under_pressure", "gas", "H280", False),
    ]


def test_fullwidth_and_alias_spellings():
    assert _items("特异性靶器官毒性-一次接触,类别３（麻醉效应）")[0] == [("stot_se", "3", "H336", False)]
    assert _items("致癌物，类别 1A")[0] == [("carcinogenicity", "1A", "H350", False)]
    assert _items("严重眼损伤/眼刺激性,类别 2")[0] == [("eye_damage", "2", "H319", False)]


def test_legacy_labels_are_reported_not_mapped():
    r = parse_hazard_text("易燃液体,类别 2\n有毒固体")
    assert [(i.hazard_class, i.category) for i in r.items] == [("flammable_liquid", "2")]
    assert "legacy_label:有毒固体" in r.review_reasons
    assert r.unparsed == ["有毒固体"]


def test_missing_category_and_empty_text():
    assert "missing_category:flammable_solid" in parse_hazard_text("易燃固体").review_reasons
    assert parse_hazard_text("").review_reasons == ["empty"]


def test_coverage_on_the_bundled_catalogue():
    """Regression floor: the parser maps at least 94% of the catalogue without review."""
    entries = json.loads(CATALOGUE.read_text(encoding="utf-8"))["chemicals"]
    clean = sum(1 for e in entries if not parse_hazard_text(e["hazard_category"]).needs_review)
    assert len(entries) == 2857
    assert clean / len(entries) >= 0.94
