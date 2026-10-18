import pytest

from app.domain.examples import load_example
from app.domain.project_spec import (
    check_process_window,
    derive_process_levers,
    effective_project_id,
    levers_to_doe_factors,
    normalize_constraints,
    normalize_requirement,
    primary_objective,
    resolve_levers,
)
from app.domain.schemas import LeverSpec, ProductDomain, Requirement, Substrate
from app.pipeline import reconstruct


def test_effective_project_id_prefers_explicit():
    req = Requirement(domain=ProductDomain.degreaser, project_id="my_project")
    assert effective_project_id(req) == "my_project"


def test_effective_project_id_from_product_type():
    req = Requirement(domain=ProductDomain.degreaser, product_type="Industrial Cleaner X")
    assert effective_project_id(req) == "industrial_cleaner_x"


def test_normalize_fills_product_type_and_application():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, substrate=Substrate.carbon_steel)
    norm = normalize_requirement(req)
    assert norm.product_type
    assert norm.application == "carbon_steel"
    assert norm.project_id


def test_primary_objective_from_objectives_list():
    req = load_example("degreaser")
    assert primary_objective(req) == "cleaning_efficiency"


def test_resolve_levers_explicit_over_legacy():
    req = load_example("anticorrosion_coating")
    levers = resolve_levers(req)
    assert levers[0].name == "Zinc phosphate"
    factors = levers_to_doe_factors(levers)
    assert factors[0].low == pytest.approx(2.0)


def test_resolve_levers_from_formulation_when_no_explicit():
    form = reconstruct.formulation_from_factors(
        ProductDomain.degreaser,
        {"Nonionic surfactant (C12-14 EO7)": 6.0, "Sodium metasilicate": 8.0},
    )
    req = Requirement(domain=ProductDomain.degreaser, levers=[])
    levers = resolve_levers(req, form=form)
    assert any(l.name == "Nonionic surfactant (C12-14 EO7)" for l in levers)


def test_load_example_unknown_raises():
    with pytest.raises(KeyError):
        load_example("nonexistent")


def test_normalize_constraints_includes_voc():
    req = load_example("anticorrosion_coating")
    merged = normalize_constraints(req)
    assert "VOC 上限" in merged


def test_normalize_constraints_from_constraint_values():
    req = Requirement(
        domain=ProductDomain.surface_treatment,
        constraint_values={"浴温上限": 50.0},
    )
    merged = normalize_constraints(req)
    assert merged["浴温上限"] == 50.0


def test_material_map_round_trip_lever_to_factor_to_snapshot():
    """P2 DOE: material_map 从 LeverSpec → DOEFactor → 快照全程不丢失。"""
    from types import SimpleNamespace

    from app.domain.project_spec import lever_snapshot_from_plan

    lever = LeverSpec(
        name="树脂",
        low=0.0,
        high=100.0,
        unit="%",
        kind="discrete",
        levels=["A", "B"],
        material_map={"A": "聚氨酯树脂X", "B": "环氧树脂Y"},
    )
    factors = levers_to_doe_factors([lever])
    # v7/v8: DOEFactor no longer carries material_map; LeverSpec is the SSOT
    # and lever_snapshot_from_plan reads it from req.levers.
    assert not hasattr(factors[0], "material_map")
    assert factors[0].kind == "discrete"
    assert factors[0].levels == ["A", "B"]

    plan = SimpleNamespace(factors=factors, runs=[], domain=None)
    snap = lever_snapshot_from_plan(plan, SimpleNamespace(levers=[lever]))
    assert snap[0]["material_map"] == {"A": "聚氨酯树脂X", "B": "环氧树脂Y"}
    assert snap[0]["kind"] == "discrete"


def test_material_map_absent_stays_none():
    """无 material_map 的 lever 透传后为 None，不污染快照。"""
    lever = LeverSpec(name="温度", low=20.0, high=80.0, unit="°C")
    factors = levers_to_doe_factors([lever])
    assert not hasattr(factors[0], "material_map")


def test_2_10_process_window_overrides_single_cure_temp():
    """补齐计划 2-10：契约工艺窗口（区间 [lo, hi]，前端真实形状）优先于单值 cure_temperature_c。"""
    from app.domain.schemas import ProductDomain, Requirement

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=200,
        process_window={"cure_temp_c": [120, 180]},
    )
    levers = derive_process_levers(req)
    assert len(levers) == 1
    assert levers[0].low == 120 and levers[0].high == 180


def test_2_10_process_window_single_number_falls_back_to_legacy():
    """补齐计划 2-10：窗口为单个数字（API/旧数据）时按单值逻辑展开 [cure-30, cure]。"""
    from app.domain.schemas import ProductDomain, Requirement

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=200,
        process_window={"cure_temp_c": 150},
    )
    levers = derive_process_levers(req)
    assert len(levers) == 1
    assert levers[0].high == 150
    assert levers[0].low == 120  # 150 - 30


def test_2_10_process_window_absent_keeps_single_value():
    """补齐计划 2-10：无窗口时单值行为不变（回归）。"""
    from app.domain.schemas import ProductDomain, Requirement

    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=200)
    levers = derive_process_levers(req)
    assert len(levers) == 1 and levers[0].high == 200


def test_2_10_check_process_window():
    """补齐计划 2-10：区间窗口内 / 列表内无警告；超出区间 / 不在列表中警告；空窗口无警告。"""
    window = {"cure_temp_c": [120, 180], "cure_time_min": [20, 40], "application_method": ["airless", "dip"]}
    assert check_process_window({"cure_temperature_c": 150, "cure_time_min": 30, "application_method": "dip"}, window) == []
    # 中文别名同样命中；超出区间 → 警告带区间
    warns = check_process_window({"固化温度": 200}, window)
    assert len(warns) == 1 and "固化温度" in warns[0] and "120~180" in warns[0]
    # 固化时间超出 + 施工方式不在列表 → 两条警告
    warns = check_process_window({"cure_time_min": 45, "application_method": "brush"}, window)
    assert len(warns) == 2
    # 单点窗口（旧数据）偏离 → 警告
    warns = check_process_window({"cure_temp_c": 151}, {"cure_temp_c": 150})
    assert len(warns) == 1 and "150" in warns[0]
    # 无窗口 / 无参数 → 无警告
    assert check_process_window({"cure_temp_c": 160}, None) == []
    assert check_process_window({"cure_temp_c": 160}, {}) == []
    assert check_process_window({}, window) == []
    # 无关因子不警告
    assert check_process_window({"树脂": 50}, window) == []


def test_2_10_dispatch_warns_on_window_deviation(tmp_path, monkeypatch):
    """补齐计划 2-10：下发任务时因子偏离契约窗口 → 警告随响应返回，任务照常创建。"""
    import uuid

    from app.db.database import Base, make_engine, make_session_factory
    from app.db.models import ProjectRow
    from app.services.lab import tasks as tasks_mod

    engine = make_engine(f"sqlite:///{tmp_path}/pw.db")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    pid = str(uuid.uuid4())
    session.add(ProjectRow(id=pid, title="pw", domain="anticorrosion_coating",
                           payload={"workspace": {"spec_contract": {"process_window": {"cure_temp_c": [120, 180]}}}}))
    session.commit()
    assert tasks_mod.project_process_window(session, pid) == {"cure_temp_c": [120, 180]}
    assert tasks_mod.project_process_window(session, "missing") is None
    warns = tasks_mod.task_process_window_warnings(session, pid, {"cure_temperature_c": 200})
    assert len(warns) == 1 and "120~180" in warns[0]
    assert tasks_mod.task_process_window_warnings(session, pid, {"cure_temperature_c": 150}) == []
