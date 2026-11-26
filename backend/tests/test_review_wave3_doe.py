"""FormuNex 新一轮全面审查（2026-11-26）修复第三波 · DOE 域定向测试。

覆盖报告 §7 第 3 批（P3）：
- D6-6 ``_plan`` 离散因子 coded 归一化到 [-1, 1]（复用 ``unit_to_coded``）；
- D6-7 ``dataframe_to_doe_plan`` 缺列抛 ``ValueError``（fail-closed）；
- D6-8 ``records_for`` 的 ``measured_only`` 口径，及两处调用方透传；
- D6-9 ``persist_experiments`` 的 ``ExperimentRow.project_id`` 写 NULL 而非 ""。

D6-9 存量 ""→NULL 归一脚本另案（须先盘点行数）；且端到端 NULL 落库
还被 ``models.py:62`` 的列定义（``Mapped[str]`` + ``default=""`` →
nullable=False）挡住——分内只改 ``persist_experiments`` 传 None，
列定义 patch 见汇报，由协调员统一落盘，DB 断言暂 skip。
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas", reason="pandas is an optional (baybe) extra")

from app.domain.schemas import DOEFactor, ExperimentRecord, ProductDomain  # noqa: E402
from app.services.engines.adapters.doe_adapter import dataframe_to_doe_plan  # noqa: E402
from app.services.training import ModelRegistry  # noqa: E402


# ── D6-6 ────────────────────────────────────────────────────────────────

def _orthogonal_plan():
    from app.api.doe_designs import _plan

    factors = [
        {"name": "固化温度", "low": 0, "high": 2, "unit": "", "kind": "discrete",
         "levels": [120, 150, 180]},
        {"name": "树脂", "low": 0, "high": 2, "unit": "", "kind": "discrete",
         "levels": ["E51", "E44", "E20"]},
        {"name": "固化时间", "low": 20, "high": 40, "unit": "min"},
    ]
    runs = [
        {"固化温度": 120, "树脂": "E51", "固化时间": 20},
        {"固化温度": 150, "树脂": "E44", "固化时间": 30},
        {"固化温度": 180, "树脂": "E20", "固化时间": 40},
    ]
    # project_id=None：_persist_doe_plan 跳过落库，只断言内存中 plan 的 coded。
    return _plan("orthogonal_L9", factors, runs, ["notes"], None)


def test_d6_6_plan_discrete_coded_in_minus_one_to_one():
    plan = _orthogonal_plan()
    for run in plan["runs"]:
        for name in ("固化温度", "树脂"):
            assert -1.0 <= run["coded"][name] <= 1.0, run["coded"]
    # 水平索引 0/1/2 → coded -1/0/+1（与 native decode() 的 [-1, 1] 口径一致）。
    assert [r["coded"]["固化温度"] for r in plan["runs"]] == [-1.0, 0.0, 1.0]
    assert [r["coded"]["树脂"] for r in plan["runs"]] == [-1.0, 0.0, 1.0]
    # 连续因子不受影响：低/中/高 → -1/0/+1。
    assert [r["coded"]["固化时间"] for r in plan["runs"]] == [-1.0, 0.0, 1.0]


def test_d6_6_plan_two_level_discrete_coded_is_endpoints():
    from app.api.doe_designs import _plan

    factors = [
        {"name": "固化剂", "low": 0, "high": 1, "unit": "", "kind": "discrete",
         "levels": ["胺 A", "胺 B"]},
    ]
    runs = [{"固化剂": "胺 A"}, {"固化剂": "胺 B"}]
    plan = _plan("orthogonal_L4", factors, runs, [], None)
    assert [r["coded"]["固化剂"] for r in plan["runs"]] == [-1.0, 1.0]


# ── D6-7 ────────────────────────────────────────────────────────────────

def _d6_7_factors():
    return [
        DOEFactor(name="树脂wt", low=40.0, high=70.0, unit="wt%"),
        DOEFactor(name="固化剂种类", low=0.0, high=1.0, unit="", kind="discrete",
                  levels=["聚酰胺", "酚醛"]),
    ]


def test_d6_7_dataframe_missing_column_raises_value_error():
    df = pd.DataFrame({"树脂wt": [50.0, 55.0]})  # 缺"固化剂种类"列
    with pytest.raises(ValueError, match="固化剂种类"):
        dataframe_to_doe_plan(df, _d6_7_factors(), "full_factorial", engine="baybe")


def test_d6_7_dataframe_full_columns_still_works():
    df = pd.DataFrame({
        "树脂wt": [50.0, 55.0],
        "固化剂种类": ["聚酰胺", "酚醛"],
    })
    plan = dataframe_to_doe_plan(df, _d6_7_factors(), "full_factorial", engine="baybe")
    assert len(plan.runs) == 2
    assert plan.runs[0].natural["固化剂种类"] == "聚酰胺"
    assert plan.runs[0].coded["固化剂种类"] == -1.0
    assert plan.runs[1].coded["固化剂种类"] == 1.0


# ── D6-8 ────────────────────────────────────────────────────────────────

def _rec(label, *, measured, source="lab", project_id=""):
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id=project_id,
        factors={"Zinc phosphate": 5.0},
        cure_temperature_c=80.0,
        measured=measured,
        source=source,
        label=label,
    )


def test_d6_8_records_for_measured_only_filters_unmeasured_and_virtual(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    domain = ProductDomain.anticorrosion_coating
    reg.add([
        _rec("measured-lab", measured={"salt_spray_hours": 500.0}),
        _rec("unmeasured-lab", measured={}),
        # csv-import 是实测通道（TRAINABLE_SOURCES），已测量时应计入。
        _rec("measured-csv", measured={"salt_spray_hours": 600.0}, source="csv-import"),
    ], retrain=False)
    # 虚拟源 add() 本就会拒收；此处直接模拟存量库里的历史虚拟行。
    reg._records.append(_rec("virtual-old", measured={"salt_spray_hours": 1.0},
                             source="baybe_opt"))

    all_recs = reg.records_for(domain)
    assert {r.label for r in all_recs} == {
        "measured-lab", "unmeasured-lab", "measured-csv", "virtual-old",
    }
    measured = reg.records_for(domain, measured_only=True)
    assert {r.label for r in measured} == {"measured-lab", "measured-csv"}


def test_d6_8_records_for_default_keeps_legacy_behaviour(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add([_rec("unmeasured-lab", measured={})], retrain=False)
    assert [r.label for r in reg.records_for(ProductDomain.anticorrosion_coating)] == [
        "unmeasured-lab"
    ]


def test_d6_8_load_prior_measurements_passes_measured_only(monkeypatch):
    from types import SimpleNamespace

    import app.services.doe_cycle_service as svc
    import app.services.training as training_mod

    seen: dict = {}

    class _StubRegistry:
        def records_for(self, domain, project_id="", *, measured_only=False):
            seen["measured_only"] = measured_only
            return [_rec("m", measured={"salt_spray_hours": 500.0})]

    monkeypatch.setattr(training_mod, "registry", _StubRegistry())
    req = SimpleNamespace(domain=ProductDomain.anticorrosion_coating, project_id="")
    assert [r.label for r in svc.load_prior_measurements(req)] == ["m"]
    assert seen["measured_only"] is True


def test_d6_8_cycle_runs_measured_count_uses_measured_only(monkeypatch, tmp_path):
    """GET /doe/cycle-runs 的 measured_count 只计已测量记录。

    stub 只有 measured_only=True 时才返回值；若调用方漏传，计数为 0。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _factory():
        return factory()

    monkeypatch.setattr("app.db.database.default_session_factory", lambda: _factory)

    from types import SimpleNamespace

    def _records_for(domain, project_id="", *, measured_only=False):
        return ["m", "m"] if measured_only else []

    monkeypatch.setattr(
        "app.services.training.registry",
        SimpleNamespace(records_for=_records_for),
    )
    from app.api import doe as doe_api

    resp = doe_api.doe_cycle_runs(project_id="proj-1", limit=20)
    n_domains = len(list(ProductDomain))
    assert resp.summary["measured_count"] == 2 * n_domains


# ── D6-9 ────────────────────────────────────────────────────────────────

@pytest.fixture()
def _doe_db(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.db.database import Base, default_engine

    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path}/doe-d6-9.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    yield
    get_settings.cache_clear()


def _experiments(n=1):
    return [
        {"run_id": i + 1, "natural_factors": {"Zinc phosphate": 5.0 + i},
         "ai_suggested": False, "infeasible": False, "infeasible_reason": ""}
        for i in range(n)
    ]


def test_d6_9_persist_experiments_passes_none_project_id(_doe_db, monkeypatch):
    """D6-9 分内改动：无项目时传给 ExperimentRow 的 project_id 是 None 而非 ""。

    端到端落库断言见下一测试——当前被 ``models.py`` 的列定义挡住
    （见 skip 原因），此处先以构造参数锁定分内修法。
    """
    from app.domain import knowledge
    from app.domain.project_workspace import default_requirement
    from app.services import doe_cycle_service as svc

    captured: list[dict] = []

    class _ExpRow:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.id = len(captured)

    class _Session:
        def add(self, obj):
            pass

        def flush(self):
            pass

    class _CM:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(svc, "ExperimentRow", _ExpRow)
    monkeypatch.setattr(svc, "default_session_factory", lambda: object())
    monkeypatch.setattr(svc, "commit_session", lambda factory: _CM())

    req = default_requirement()
    assert req.project_id == ""
    out = svc.persist_experiments(req, _experiments(1), [knowledge.baseline_formulation(req)])
    assert out["count"] == 1
    assert len(captured) == 1
    assert captured[0]["project_id"] is None  # 不再是 ""


def test_d6_9_persist_experiments_project_id_null_in_db(_doe_db):
    from sqlalchemy import select

    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow
    from app.domain import knowledge
    from app.domain.project_workspace import default_requirement
    from app.services import doe_cycle_service as svc

    req = default_requirement()
    assert req.project_id == ""
    out = svc.persist_experiments(req, _experiments(1), [knowledge.baseline_formulation(req)])
    assert out["count"] == 1
    with default_session_factory()() as s:
        row = s.execute(select(ExperimentRow)).scalars().first()
    assert row is not None
    assert row.project_id is None  # 不再是 ""


def test_d6_9_persist_experiments_keeps_named_project_id(_doe_db):
    from sqlalchemy import select

    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow
    from app.domain import knowledge
    from app.domain.project_workspace import default_requirement
    from app.services import doe_cycle_service as svc

    req = default_requirement().model_copy(update={"project_id": "proj-x"})
    out = svc.persist_experiments(req, _experiments(1), [knowledge.baseline_formulation(req)])
    assert out["count"] == 1
    with default_session_factory()() as s:
        row = s.execute(select(ExperimentRow)).scalars().first()
    assert row is not None
    assert row.project_id == "proj-x"
