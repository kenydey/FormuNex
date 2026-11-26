"""第二波 P2（A3 DOE）：D6-3 紧约束 simplex 去重、D6-4 全钉死组分单点设计。

D6-3：pyDOE simplex 在紧约束下把不同比例点截到同一边界点，
``matrix_to_doe_plan`` 原样输出重复 run 且 notes 不提。修法：mixture
分支按 natural 去重、重编号，notes 写明合并数；去重后 run 数少于
二次 Scheffé 模型参数数时提示改用约束混料设计。
D6-4：P1-C16 修而不彻底 —— 全钉死组分（span==0）只修了 pseudo 除零，
``design`` 仍走 select，默认 n=8 对 1 个候选点报 422。修法：span==0
且候选集只剩唯一一点时，在默认 n 计算之前短路，直接返回单点设计。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.domain.schemas import DOEFactor
from app.services.doe_designs import constrained_mixture as cm
from app.services.engines.adapters.doe_adapter import matrix_to_doe_plan

# D6-3 复现用例：A 40～50、B 10～90，总量取 Σ 中点 = 95。
TIGHT = [
    DOEFactor(name="A", low=40, high=50, unit="wt%"),
    DOEFactor(name="B", low=10, high=90, unit="wt%"),
]


def _simplex_2() -> np.ndarray:
    """simplex_lattice / simplex_centroid 在 k=2 时的 3 行（(1,0),(0,1),(0.5,0.5)）。"""
    return np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])


def test_d6_3_tight_simplex_lattice_dedupes_collapsed_runs():
    plan = matrix_to_doe_plan(_simplex_2(), TIGHT, "simplex_lattice", engine="pydoe")
    naturals = {(r.natural["A"], r.natural["B"]) for r in plan.runs}
    assert naturals == {(50.0, 45.0), (40.0, 55.0)}
    assert len(plan.runs) == 2
    assert [r.run_id for r in plan.runs] == [1, 2]
    assert "2 runs" in plan.notes
    assert "1 个重复点已合并" in plan.notes
    assert "紧约束" in plan.notes


def test_d6_3_tight_simplex_centroid_dedupes_the_same_way():
    plan = matrix_to_doe_plan(_simplex_2(), TIGHT, "simplex_centroid", engine="pydoe")
    assert len(plan.runs) == 2
    assert len({(r.natural["A"], r.natural["B"]) for r in plan.runs}) == 2
    assert "重复点已合并" in plan.notes


def test_d6_3_dedup_below_model_parameters_suggests_constrained_mixture():
    """去重后 2 runs < 二次 Scheffé 参数数 3：notes 提示改用约束混料设计。"""
    plan = matrix_to_doe_plan(_simplex_2(), TIGHT, "simplex_lattice", engine="pydoe")
    assert "约束混料设计" in plan.notes
    assert "/doe/designs/mixture" in plan.notes


def test_d6_3_no_collapse_leaves_plan_untouched():
    """无坍缩时不改 run 数、不加去重提示（回归锚）。"""
    wide = [
        DOEFactor(name="A", low=10, high=90, unit="wt%"),
        DOEFactor(name="B", low=10, high=90, unit="wt%"),
    ]
    plan = matrix_to_doe_plan(_simplex_2(), wide, "simplex_lattice", engine="pydoe")
    assert len(plan.runs) == 3
    assert "重复点" not in plan.notes


def test_d6_3_dedup_is_mixture_only():
    """去重只在 mixture 分支：非混料设计的重复行原样保留（作用域锁定）。"""
    factors = [DOEFactor(name="x", low=0, high=10), DOEFactor(name="y", low=0, high=10)]
    matrix = np.array([[0.1, 0.2], [0.1, 0.2], [0.5, 0.5]])
    plan = matrix_to_doe_plan(matrix, factors, "lhs", engine="pydoe")
    assert len(plan.runs) == 3


def test_d6_4_fully_pinned_components_return_single_point_design():
    """D6-4：A 60～60、B 40～40（span==0）默认调用返回单点设计，不再 422。"""
    d = cm.design([cm.Component("A", 60, 60), cm.Component("B", 40, 40)], total=100)
    assert len(d.runs) == 1
    assert d.candidates == 1
    assert d.vertices == 1
    run = d.runs[0]
    assert run["A"] == 60.0 and run["B"] == 40.0
    assert run["_run"] == 1
    assert d.model["parameters"] == 3  # 二次 Scheffé，2 组分
    assert d.model["estimable"] is False
    assert d.model["runs"] == 1
    assert any("钉死" in n for n in d.notes)
    assert any("不可估" in n for n in d.notes)


def test_d6_4_short_circuit_ignores_explicit_n_runs():
    """短路在默认 n 计算之前：显式 n_runs=8 也不经过 select、不 422。"""
    d = cm.design([cm.Component("A", 60, 60), cm.Component("B", 40, 40)],
                  total=100, n_runs=8)
    assert len(d.runs) == 1
    assert d.runs[0]["A"] == 60.0 and d.runs[0]["B"] == 40.0


def test_d6_4_pinned_linear_order_still_single_point():
    d = cm.design([cm.Component("A", 60, 60), cm.Component("B", 40, 40)],
                  total=100, mixture_order="linear")
    assert len(d.runs) == 1
    assert d.model["parameters"] == 2
    assert d.model["estimable"] is False


def test_d6_4_api_returns_200_instead_of_422(tmp_path, monkeypatch):
    """端到端：POST /doe/designs/mixture 全钉死组分不再 422。"""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path}/doe.db")
    get_settings.cache_clear()
    from app.db.database import Base, default_engine

    Base.metadata.create_all(default_engine())
    try:
        r = TestClient(app).post(
            "/api/doe/designs/mixture",
            json={"components": [
                {"name": "A", "low": 60, "high": 60},
                {"name": "B", "low": 40, "high": 40},
            ]},
        )
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["runs"]) == 1
    assert out["runs"][0]["A"] == 60.0 and out["runs"][0]["B"] == 40.0
    assert out["model"]["estimable"] is False
    assert any("钉死" in n for n in out["notes"])
