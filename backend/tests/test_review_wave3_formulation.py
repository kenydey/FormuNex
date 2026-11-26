"""FormuNex 新一轮全面审查（2026-11-26）修复第三波 · 配方域定向测试。

覆盖报告 §6 第 3 批（P3）：
- P3-1 ``compare_classifications`` 漏同 H 代码的子类别变化：比较中加入按危害类别的
  severity 序（复用 ``label_elements.severity``），子类别变严即 ``upgraded=True``；
  象形图新增亦计入。``_classification_summary`` 在结果带 hazard outcomes 时附
  ``categories``（H 代码 → 类别）。
- P3-2 ``find_substitutes`` 按层拆为子函数（L1/L2/L3/SureChEMBL/L4/预览层），不改行为：
  本文件的 P3-2 测试只钉签名与顶层键，行为验证即现有 ``tests/test_substitution.py`` 全过。

关于报告示例的方向说明（重要）：报告 §6 P3-1 举例“皮肤腐蚀 1A→1C、眼损伤 2A→2B”
为“变严”，但按 GHS 与本仓库 ``plm.ghs.model.CATEGORY_ORDER``（序值越小越严；
``bridging.py`` 取最严亦用 ``min(..., key=severity)``），1A 严于 1C、2A 严于 2B，
即 1A→1C / 2A→2B 实际是变松，不是变严。本实现以 ``severity()`` 序为准（变严 =
序值变小，如 1C→1A、2B→2A），并用测试把两个方向都钉死；若协调员确认报告字面
方向为准，须把 ``compare_classifications`` 里的 ``<`` 改为 ``>``（一行），但那将
与所复用的 ``severity`` 语义相逆，特此留待确认，不在本轮自行翻转。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.schemas import ProductDomain, Requirement
from app.pipeline import reconstruct
from app.services.substitution import (
    _classification_summary,
    compare_classifications,
    find_substitutes,
)


def _req(voc: float = 420) -> Requirement:
    return Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=voc)


def _genome(req: Requirement | None = None):
    return reconstruct.genome_from_requirement(req or _req())


def _slot_of(genome, role: str) -> int:
    return next(i for i, s in enumerate(genome.slots) if s.role == role)


def _hazard(hazard_class: str, category: str, h_code: str) -> SimpleNamespace:
    return SimpleNamespace(hazard_class=hazard_class, category=category, h_codes=[h_code])


# ── P3-1：子类别变化 ─────────────────────────────────────────────────────────


def test_p3_1_subcategory_tightening_is_upgrade():
    """P3-1：同 H 代码子类别变严（severity 序值变小）即 upgraded。

    信号词与 H 代码集完全相同，唯一变化是类别：皮肤腐蚀 1C→1A、眼损伤 2B→2A。
    """
    before = {
        "signal_word": "警告", "h_codes": ["H314", "H319"],
        "pictograms": ["GHS05", "GHS07"],
        "categories": {"H314": "1C", "H319": "2B"},
    }
    after = {
        "signal_word": "警告", "h_codes": ["H314", "H319"],
        "pictograms": ["GHS05", "GHS07"],
        "categories": {"H314": "1A", "H319": "2A"},
    }
    out = compare_classifications(before, after)
    assert out["changed"] is True and out["upgraded"] is True
    assert out["added_h_codes"] == [] and out["removed_h_codes"] == []
    # 单类别变严同样升级
    one = compare_classifications(
        {"signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1C"}},
        {"signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1A"}},
    )
    assert one["changed"] is True and one["upgraded"] is True


def test_p3_1_subcategory_loosening_is_change_not_upgrade():
    """P3-1：子类别变松（1A→1C、2A→2B）是变化，但不是升级。

    见模块 docstring：报告示例把该方向写反了；按 GHS 与 severity() 序，1A 严于 1C。
    """
    out = compare_classifications(
        {"signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1A"}},
        {"signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1C"}},
    )
    assert out["changed"] is True and out["upgraded"] is False
    out2 = compare_classifications(
        {"signal_word": "警告", "h_codes": ["H319"], "categories": {"H319": "2A"}},
        {"signal_word": "警告", "h_codes": ["H319"], "categories": {"H319": "2B"}},
    )
    assert out2["changed"] is True and out2["upgraded"] is False


def test_p3_1_same_subcategory_is_no_change():
    """P3-1：类别相同（即使摘要带 categories）仍判无变化。"""
    summary = {
        "signal_word": "警告", "h_codes": ["H314"], "pictograms": ["GHS05"],
        "categories": {"H314": "1B"},
    }
    assert compare_classifications(summary, dict(summary)) == {
        "changed": False, "upgraded": False, "added_h_codes": [], "removed_h_codes": [],
        "signal_before": "警告", "signal_after": "警告",
    }


def test_p3_1_pictogram_added_is_upgrade_removed_is_change_only():
    """P3-1：象形图新增计入升级；仅移除象形图是变化、不是升级。"""
    base = {"signal_word": "警告", "h_codes": ["H226"], "pictograms": []}
    added = compare_classifications(
        base, {"signal_word": "警告", "h_codes": ["H226"], "pictograms": ["GHS02"]})
    assert added["changed"] is True and added["upgraded"] is True
    removed = compare_classifications(
        {"signal_word": "警告", "h_codes": ["H226"], "pictograms": ["GHS02"]}, base)
    assert removed["changed"] is True and removed["upgraded"] is False


def test_p3_1_no_categories_falls_back_to_old_rules():
    """P3-1：旧形态摘要（无 categories 键）退回信号词 + H 代码口径，不误报。"""
    base = {"signal_word": "警告", "h_codes": ["H226"]}
    assert compare_classifications(base, dict(base))["changed"] is False
    up = compare_classifications(base, {"signal_word": "危险", "h_codes": ["H226", "H314"]})
    assert up["upgraded"] and up["added_h_codes"] == ["H314"]
    down = compare_classifications(base, {"signal_word": None, "h_codes": []})
    assert down["changed"] and not down["upgraded"]


def test_p3_1_summary_carries_categories_only_when_hazards_present():
    """P3-1：_classification_summary 只在结果带 hazards 时附 categories。

    无 hazards 的结果保持原有三键形态（现有 test_2_7 的全等断言不受影响）；
    同 H 代码分属多类时取最严类别。
    """
    bare = SimpleNamespace(signal_word="警告", h_codes=["H226"], pictograms=[])
    assert _classification_summary(bare) == {
        "signal_word": "警告", "h_codes": ["H226"], "pictograms": [],
    }
    assert "categories" not in _classification_summary(bare)
    with_hazards = SimpleNamespace(
        signal_word="警告", h_codes=["H314", "H319"], pictograms=[{"code": "GHS05"}],
        hazards=[
            _hazard("skin_corrosion", "1C", "H314"),
            _hazard("eye_damage", "2A", "H319"),
            # 同 H314 再出现一次更严类别 → 取 1A
            _hazard("skin_corrosion", "1A", "H314"),
        ],
    )
    summary = _classification_summary(with_hazards)
    assert summary["categories"] == {"H314": "1A", "H319": "2A"}
    assert summary["pictograms"] == ["GHS05"]


def test_p3_1_preview_flags_subcategory_only_upgrade(monkeypatch):
    """P3-1：分类预览里仅子类别变严（信号词/H 代码/象形图全同）也置 upgraded。"""
    calls = {"n": 0}

    def _fake_classify(session_, items, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(
                signal_word="警告", h_codes=["H314"], pictograms=[],
                hazards=[_hazard("skin_corrosion", "1C", "H314")]), {}
        return SimpleNamespace(
            signal_word="警告", h_codes=["H314"], pictograms=[],
            hazards=[_hazard("skin_corrosion", "1A", "H314")]), {}

    monkeypatch.setattr("app.services.plm.ghs.service.build_and_classify", _fake_classify)
    genome = _genome()
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), _req(), include_external=False,
        include_literature=False, include_surechembl=False, include_llm=False,
        session=object(), classification_preview_limit=2,
    )
    assert report["candidates"]
    previewed = [c for c in report["candidates"][:2] if "classification_after" in c]
    assert len(previewed) == len(report["candidates"][:2])
    for c in previewed:
        assert c["classification_before"]["categories"] == {"H314": "1C"}
        assert c["classification_after"]["categories"] == {"H314": "1A"}
        assert c["classification_changed"] is True and c["classification_upgraded"] is True


def test_p3_1_trigger_fires_on_subcategory_upgrade(tmp_path, monkeypatch):
    """P3-1：触发扫描里仅子类别变严也产生 classification_upgrade，且 detail 说明原因。"""
    from app.db.database import Base, make_engine, make_session_factory
    from app.services.substitution import scan_substitution_triggers

    engine = make_engine(f"sqlite:///{tmp_path}/trig-p3-1.db")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    genome = _genome()

    def _fake_classify(session_, items, **kw):
        return SimpleNamespace(
            signal_word="警告", h_codes=["H314"], pictograms=[],
            hazards=[_hazard("skin_corrosion", "1A", "H314")]), {}

    monkeypatch.setattr("app.services.plm.ghs.service.build_and_classify", _fake_classify)
    out = scan_substitution_triggers(
        {"baseline": genome}, _req(), session=session,
        baseline_classifications={"baseline": {
            "signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1C"}}},
    )
    ups = [t for t in out["triggers"] if t["type"] == "classification_upgrade"]
    assert len(ups) == 1
    assert ups[0]["added_h_codes"] == []
    assert "子类别变严" in ups[0]["detail"] and "H314" in ups[0]["detail"]
    assert ups[0]["classification_before"]["categories"] == {"H314": "1C"}
    # 类别相同 → 不触发
    out2 = scan_substitution_triggers(
        {"baseline": genome}, _req(), session=session,
        baseline_classifications={"baseline": {
            "signal_word": "警告", "h_codes": ["H314"], "categories": {"H314": "1A"}}},
    )
    assert "classification_upgrade" not in out2["counts"]


# ── P3-2：拆分不改行为（行为验证见 tests/test_substitution.py 全过） ────────


def test_p3_2_find_substitutes_signature_and_top_level_keys_unchanged():
    """P3-2：find_substitutes 签名与返回顶层键与拆分前一致；层子函数存在。"""
    import inspect

    from app.services.substitution import (
        _catalog_layer,
        _classification_preview,
        _external_layer,
        _literature_layer,
        _llm_layer,
        _surechembl_layer,
    )

    assert list(inspect.signature(find_substitutes).parameters) == [
        "genome", "slot_index", "req", "limit", "include_unavailable", "include_external",
        "external_limit", "similarity_threshold", "include_literature", "literature_limit",
        "include_surechembl", "surechembl_limit", "include_llm", "llm_limit", "session",
        "classification_preview_limit",
    ]
    for helper in (_catalog_layer, _classification_preview, _external_layer,
                   _literature_layer, _surechembl_layer, _llm_layer):
        assert callable(helper)
    genome = _genome()
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), _req(), include_external=False,
        include_literature=False, include_surechembl=False, include_llm=False,
    )
    assert set(report) == {
        "original", "original_in_catalog", "slot_index", "role", "substitute_group",
        "base_metrics", "candidates", "total_considered", "identity", "external",
        "external_meta", "literature", "literature_meta", "surechembl", "surechembl_meta",
        "llm", "llm_meta", "layers_used",
    }
    assert report["layers_used"] == ["catalog"]
