"""Colorimetry service — CIELAB color estimate and CIEDE2000 color difference.

补齐 2-21（口径统一）：ΔE 有两个独立口径，绝不静默混用——
  - ``delta_e_2000``: CIEDE2000，colour-science 未安装时返回 None
    （旧行为静默 fallback 到 CIE76，造成同一调用在不同机器返回不同口径）；
  - ``delta_e_ab``: CIE76 欧氏距离，始终可用；
  - ``delta_e``: 统一口径选择器（见下），非法 method → ValueError。
``color_metrics`` 返回全部口径键 + ``delta_e_method`` 注明实际口径，
legacy ``delta_e`` 兼容键恒为 float（= delta_e_00 若有，否则 delta_e_ab）。
The formulation color is estimated as the pigment-volume-weighted mean of
the particulate pigments' tabulated CIELAB values (a screening-level
approximation, not a spectral simulation).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from .errors import log_handled_exception, optional_import
from ..domain.chemistry import _PIGMENT_ROLES, _density_gcm3
from ..domain.schemas import Formulation

logger = logging.getLogger(__name__)

# Reference white for the ΔE comparison (perfect diffuse white).
WHITE_LAB: tuple[float, float, float] = (100.0, 0.0, 0.0)


@lru_cache(maxsize=1)
def _colour_available() -> bool:
    """Whether ``colour-science`` is installed — probed once per process.

    Every ``predict()`` of a formulation with a pigment ends up here, and an optimisation run predicts thousands
    of candidates. The probe used to import (and fail) on every call and log a WARNING each time, so a default
    install — which does not have the optional ``color`` extra — wrote one "optional feature check: No module
    named 'colour'" line per prediction.
    """
    return optional_import("colour")


def delta_e_2000(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float]
) -> float | None:
    """CIEDE2000 color difference ΔE₀₀ between two CIELAB triples.

    Returns None when ``colour-science`` is absent — never silently falls
    back to CIE76 (the old behaviour made the same call return different
    metrics on different machines). Use ``delta_e_ab`` for the always-available
    CIE76 distance, or ``delta_e`` to pick explicitly.
    """
    if _colour_available():
        try:  # pragma: no cover - requires colour-science
            import numpy as np
            from colour.difference import delta_E_CIE2000

            return round(float(delta_E_CIE2000(np.array(lab1), np.array(lab2))), 3)
        except Exception as exc:
            log_handled_exception(logger, exc, "handled exception")
    return None


def delta_e_ab(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float]
) -> float:
    """CIE76 ΔE*ab: Euclidean distance in L*a*b*. Always available."""
    return round(sum((a - b) ** 2 for a, b in zip(lab1, lab2)) ** 0.5, 3)


def delta_e(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float],
    method: str = "cie2000",
) -> float:
    """ΔE with an explicit metric: ``method="cie2000"`` or ``"cie76"``.

    cie2000 without colour-science → CIE76 fallback is NOT silent here:
    the caller asked for cie2000 explicitly, so the absence raises through
    delta_e_2000's None — callers that need a guaranteed float should pass
    method="cie76" or use ``delta_e_ab`` directly.
    """
    if method == "cie2000":
        out = delta_e_2000(lab1, lab2)
        if out is None:
            raise ValueError("colour-science 未安装，无法计算 CIEDE2000；改用 method='cie76'")
        return out
    if method == "cie76":
        return delta_e_ab(lab1, lab2)
    raise ValueError(f"未知 ΔE 口径 {method!r}，可选 cie2000/cie76")


def mixture_lab(form: Formulation) -> tuple[float, float, float] | None:
    """Pigment-volume-weighted CIELAB color of a formulation.

    Returns None when no pigment in the formulation has a tabulated ``lab``
    value, so callers can skip color reporting rather than guess.
    """
    from ..domain.knowledge import RAW_MATERIALS

    L = a = b = vol_total = 0.0
    for ing in form.ingredients:
        if ing.role not in _PIGMENT_ROLES:
            continue
        lab = RAW_MATERIALS.get(ing.name, {}).get("lab")
        if not lab:
            continue
        vol = ing.weight_pct / _density_gcm3(ing.name, ing.role)
        L += lab[0] * vol
        a += lab[1] * vol
        b += lab[2] * vol
        vol_total += vol
    if vol_total <= 0:
        return None
    return (round(L / vol_total, 2), round(a / vol_total, 2), round(b / vol_total, 2))


def color_metrics(form: Formulation) -> dict[str, float | str | None]:
    """Color descriptors for a formulation: ``lab_L/a/b`` and ΔE vs white.

    Returns the same keys with ``None`` values when the formulation has no
    color-bearing pigment (P1-15): callers used to receive ``{}`` and the
    cockpit ``/color`` endpoint spread it verbatim, so the frontend rendered
    ``undefined.toFixed(...)`` and the whole color tab crashed. Explicit nulls
    let the frontend guard and label the row instead. The predictor still
    drops non-numeric values, so ``predicted`` is unchanged for such formulas.
    Keys: L/a/b (aliases of lab_L/a/b), delta_e_00 (None without
    colour-science or without pigment), delta_e_ab (a float only when a Lab
    value exists), delta_e (compat: delta_e_00 if available else delta_e_ab),
    delta_e_method.
    """
    lab = mixture_lab(form)
    if lab is None:
        return {
            "lab_L": None, "lab_a": None, "lab_b": None,
            "L": None, "a": None, "b": None,
            "delta_e_00": None, "delta_e_ab": None, "delta_e": None,
            "delta_e_method": None,
        }
    de00 = delta_e_2000(lab, WHITE_LAB)
    deab = delta_e_ab(lab, WHITE_LAB)
    return {
        "lab_L": lab[0],
        "lab_a": lab[1],
        "lab_b": lab[2],
        "L": lab[0],
        "a": lab[1],
        "b": lab[2],
        "delta_e_00": de00,
        "delta_e_ab": deab,
        "delta_e": de00 if de00 is not None else deab,
        "delta_e_method": "cie2000" if de00 is not None else "cie76",
    }
