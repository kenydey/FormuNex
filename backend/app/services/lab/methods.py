"""Test-method library and instrument register (方案 §5.3.1, M7).

A measurement without a method is not a result: every recorded value names a
method, which fixes the metric, the unit, the GB/T 8170 rounding and the valid
range. The built-in library covers routine coating / surface-treatment tests;
standard numbers are given without year — the laboratory confirms the edition
in force and may edit or add methods.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...db.models_lab import InstrumentRow, TestMethodRow


class LabError(ValueError):
    """Invalid lab data or a request not valid in the current state."""


class LabForbidden(PermissionError):
    pass


# code, name_zh, name_en, metric, unit, decimals, valid_min, valid_max
BUILTIN_METHODS: tuple[tuple[str, str, str, str, str, int | None, float | None, float | None], ...] = (
    ("GB/T 10125", "人造气氛腐蚀试验 盐雾试验", "Salt spray test", "salt_spray_hours", "h", 0, 0, 10000),
    ("GB/T 9286", "色漆和清漆 划格试验", "Cross-cut test", "adhesion_grade", "级", 0, 0, 5),
    ("GB/T 5210", "色漆和清漆 拉开法附着力试验", "Pull-off adhesion", "adhesion_mpa", "MPa", 1, 0, 50),
    ("GB/T 6739", "色漆和清漆 铅笔法测定漆膜硬度", "Pencil hardness", "pencil_hardness_idx", "", 0, 0, 20),
    ("GB/T 1728", "漆膜、腻子膜干燥时间测定法", "Drying time", "dry_time_min", "min", 0, 0, 10080),
    ("GB/T 1725", "色漆、清漆和塑料 不挥发物含量的测定", "Non-volatile content", "solids_content_pct", "%", 1, 0, 100),
    ("GB/T 6750", "色漆和清漆 密度的测定 比重瓶法", "Density (pyknometer)", "density_g_cm3", "g/cm³", 2, 0.5, 5),
    ("GB/T 1723", "涂料粘度测定法（涂-4 杯）", "Viscosity (flow cup)", "viscosity_s", "s", 0, 0, 1000),
    ("GB/T 9751.1", "色漆和清漆 旋转黏度计法", "Viscosity (rotational)", "viscosity_mpas", "mPa·s", 0, 0, 1_000_000),
    ("GB/T 6753.1", "色漆、清漆和印刷油墨 研磨细度的测定", "Fineness of grind", "fineness_um", "μm", 0, 0, 200),
    ("GB/T 1732", "漆膜耐冲击测定法", "Impact resistance", "impact_cm", "cm", 0, 0, 100),
    ("GB/T 6742", "色漆和清漆 弯曲试验（圆柱轴）", "Bend test", "bend_mm", "mm", 0, 0, 50),
    ("GB/T 13452.2", "色漆和清漆 漆膜厚度的测定", "Film thickness", "film_thickness_um", "μm", 0, 0, 2000),
    ("GB/T 9754", "色漆和清漆 20°、60°和 85°镜面光泽的测定", "Specular gloss", "gloss_60", "GU", 0, 0, 200),
    ("GB/T 11186.3", "涂膜颜色的测量方法 色差计算", "Colour difference", "delta_e", "", 2, 0, 100),
    ("GB/T 261", "闪点的测定 宾斯基-马丁闭口杯法", "Flash point (Pensky-Martens)", "flash_point_c", "℃", 0, -30, 400),
    ("GB/T 23986", "色漆和清漆 挥发性有机化合物（VOC）含量的测定 气相色谱法", "VOC (GC)", "voc_g_l", "g/L", 0, 0, 1000),
    ("GB/T 9724", "化学试剂 pH 值测定通则", "pH", "ph", "", 1, 0, 14),
    ("GB/T 265", "石油产品运动粘度测定法", "Kinematic viscosity", "kinematic_viscosity_40c", "mm²/s", 2, 0, 100000),
    ("GB/T 6682", "分析实验室用水规格和试验方法（电导率）", "Conductivity", "conductivity_us_cm", "μS/cm", 1, 0, 200000),
)


def seed_methods(session: Session) -> int:
    """Insert the built-in methods that are missing (existing ones, possibly edited, are kept)."""
    existing = set(session.execute(select(TestMethodRow.code)).scalars())
    n = 0
    for code, zh, en, metric, unit, decimals, lo, hi in BUILTIN_METHODS:
        if code in existing:
            continue
        session.add(TestMethodRow(id=str(uuid.uuid4()), code=code, name_zh=zh, name_en=en, metric=metric, unit=unit,
                                  rounding={"decimals": decimals} if decimals is not None else {},
                                  valid_min=lo, valid_max=hi))
        n += 1
    session.flush()
    return n


def method_dict(m: TestMethodRow) -> dict[str, Any]:
    return {"id": m.id, "code": m.code, "name_zh": m.name_zh, "name_en": m.name_en, "metric": m.metric,
            "unit": m.unit, "rounding": m.rounding or {}, "valid_min": m.valid_min, "valid_max": m.valid_max,
            "active": m.active}


def save_method(session: Session, data: dict[str, Any], method_id: str | None = None) -> TestMethodRow:
    if method_id:
        m = session.get(TestMethodRow, method_id)
        if m is None:
            raise LabError("方法不存在")
    else:
        if session.execute(select(TestMethodRow.id).where(TestMethodRow.code == data["code"])).first():
            raise LabError(f"方法 {data['code']} 已存在")
        m = TestMethodRow(id=str(uuid.uuid4()))
        session.add(m)
    for k in ("code", "name_zh", "name_en", "metric", "unit", "rounding", "valid_min", "valid_max", "active"):
        if k in data and data[k] is not None:
            setattr(m, k, data[k])
    if m.valid_min is not None and m.valid_max is not None and m.valid_min > m.valid_max:
        raise LabError("有效范围下限大于上限")
    session.flush()
    return m


def resolve_method(session: Session, method_id: str | None = None, code: str | None = None) -> TestMethodRow:
    m = session.get(TestMethodRow, method_id) if method_id else (
        session.execute(select(TestMethodRow).where(TestMethodRow.code == code)).scalar_one_or_none() if code else None)
    if m is None:
        raise LabError(f"未知检测方法 {method_id or code or ''}（测量值必须注明方法）")
    if not m.active:
        raise LabError(f"检测方法 {m.code} 已停用")
    return m


# --------------------------------------------------------------------------- instruments


@dataclass
class Calibration:
    state: str  # ok | due_soon | overdue | unknown
    due: datetime | None


def calibration(instr: InstrumentRow, now: datetime | None = None) -> Calibration:
    now = now or utcnow()
    if instr.calibration_due is None:
        return Calibration("unknown", None)
    if instr.calibration_due < now:
        return Calibration("overdue", instr.calibration_due)
    if (instr.calibration_due - now).days <= 30:
        return Calibration("due_soon", instr.calibration_due)
    return Calibration("ok", instr.calibration_due)


def instrument_dict(i: InstrumentRow) -> dict[str, Any]:
    cal = calibration(i)
    return {"id": i.id, "asset_no": i.asset_no, "name": i.name, "model": i.model, "location": i.location,
            "calibrated_at": i.calibrated_at.isoformat() if i.calibrated_at else None,
            "calibration_due": i.calibration_due.isoformat() if i.calibration_due else None,
            "calibration_state": cal.state, "parser": i.parser, "active": i.active}


def save_instrument(session: Session, data: dict[str, Any], instrument_id: str | None = None) -> InstrumentRow:
    if instrument_id:
        i = session.get(InstrumentRow, instrument_id)
        if i is None:
            raise LabError("仪器不存在")
    else:
        if session.execute(select(InstrumentRow.id).where(InstrumentRow.asset_no == data["asset_no"])).first():
            raise LabError(f"资产编号 {data['asset_no']} 已存在")
        i = InstrumentRow(id=str(uuid.uuid4()))
        session.add(i)
    for k in ("asset_no", "name", "model", "location", "calibrated_at", "calibration_due", "parser", "active"):
        if k in data and data[k] is not None:
            setattr(i, k, data[k])
    session.flush()
    return i
