"""B-7: 收敛判定统一入口。

``auto_loop``（闭环迭代）、``workbench_loop``（工作台闭环任务经由
``auto_loop.loop_iterate``）与 ``doe_cycle_service``（DOE 周期硬停）此前
各有一份收敛判定逻辑（RMSE 平台期 / 目标达成 / 最优测量值提取），
现统一收敛到本模块。调用方只从这里 import。

约定：
- ``evaluate_convergence`` 返回 ``(converged, reason)``，
  ``reason`` ∈ ``{"target_achieved", "rmse_plateau", ""}``；
  （``doe_cycle_runs.convergence_reason`` 列还可能有 ``"budget_exhausted"``，
  那是 DOE 周期硬停写入的，不经过本模块。）
- 目标达成优先于平台期（与原 auto_loop 语义一致）；
- ``None`` 数据永不判收敛（fail-open）。
- P2-3：RMSE 平台期须有训练代际戳佐证 —— 快照逐字节相同但窗口内
  零重训时不判收敛（"没学"≠"学不动了"）。代际戳由调用方随历史并行
  传入（``generations``）；未传时沿用旧语义（legacy 兼容）。
- P2-6：目标达成只看可训练来源（``training.TRAINABLE_SOURCES``）的
  实测值；虚拟/未知来源记录永不触发 ``target_achieved``。
"""
from __future__ import annotations

from ..domain.schemas import ObjectiveSpec, Requirement
from .training import TRAINABLE_SOURCES

#: 收敛原因常量（与 doe_cycle_runs.convergence_reason 列取值一致）。
REASON_TARGET_ACHIEVED = "target_achieved"
REASON_RMSE_PLATEAU = "rmse_plateau"
REASON_NONE = ""

#: plateau 检测只看近 N 轮，避免跨长循环无限增长内存（S7）。
_PLATEAU_HISTORY_CAP = 50


def rmse_generation_stamp(model_infos: list | None) -> str | None:
    """P2-3: 训练代际戳 —— 同一模型集合重训后必然变化的指纹。

    由每项 ``domain:metric@version|trained_at:n_samples`` 组成；
    ``version_id``（持久化开启时）或 ``trained_at``（每次 fit 都刷新）
    保证重训改变戳；``n_samples`` 兜底同一秒内两次重训的戳碰撞。
    无模型时返回 ``None``（无法判定代际，调用方按 legacy 语义处理）。
    """
    infos = list(model_infos or [])
    if not infos:
        return None
    parts = []
    for m in sorted(
        infos, key=lambda x: (str(getattr(x, "domain", "")), str(getattr(x, "metric", "")))
    ):
        ver = getattr(m, "version_id", None) or getattr(m, "trained_at", None) or "-"
        parts.append(
            f"{getattr(m, 'domain', '')}:{getattr(m, 'metric', '')}"
            f"@{ver}:{getattr(m, 'n_samples', 0)}"
        )
    return "|".join(parts)


def rmse_plateau_detected(
    history: list[dict[str, float]],
    *,
    eps: float,
    patience: int,
    generations: list | None = None,
) -> bool:
    """True when the last ``patience`` consecutive RMSE steps are flat for all metrics.

    P2-3: ``generations`` 与 ``history`` 等长的并行代际戳（见
    :func:`rmse_generation_stamp`）。传入时，平台期还要求窗口内至少
    发生一次代际变化 —— 快照扁平但零重训（新数据不足 / ``auto_retrain``
    关闭 / 批次未执行）时返回 False。``generations`` 为 ``None``（旧
    调用方）或长度失配 / 窗口内有 ``None`` 时，fail-open 沿用旧语义
    （只看扁平），不静默改判。
    """
    if patience < 1 or len(history) < patience + 1:
        return False
    metrics: set[str] = set()
    for snap in history:
        metrics.update(snap.keys())
    if not metrics:
        return False
    recent = history[-(patience + 1) :]
    for i in range(1, len(recent)):
        prev, curr = recent[i - 1], recent[i]
        for metric in metrics:
            if metric not in prev or metric not in curr:
                return False
            if abs(curr[metric] - prev[metric]) >= eps:
                return False
    if generations is not None:
        if len(generations) != len(history):
            return True
        window_gens = generations[-(patience + 1) :]
        if any(g is None for g in window_gens):
            return True
        if len(set(window_gens)) < 2:
            return False
    return True


def target_achieved(best_so_far: float | None, objective: ObjectiveSpec) -> bool:
    """P2-4: True when the measured best already meets the objective target.

    Pure function. ``None`` best or ``None`` target → False (fail-open:
    never claim convergence without data).
    """
    target = objective.target_value
    if best_so_far is None or target is None:
        return False
    if objective.direction == "minimize":
        return best_so_far <= target
    return best_so_far >= target


def best_objective_value(
    records: list | None, metric: str, direction: str = "maximize"
) -> float | None:
    """P2-4: best measured value of ``metric`` across prior records.

    P2-6: 只看可训练来源（``training.TRAINABLE_SOURCES``：lab /
    workbench / csv-import）的实测值。虚拟源（``predictor_virtual`` /
    ``baybe_opt``）与未知来源的 measured 值永不触发目标达成 —— 否则
    一条虚拟记录的超目标值就能停掉整个闭环。``source`` 缺失/为空按
    旧默认值 ``"lab"`` 计（与 measurements_adapter 的 P0-5 口径一致）。
    """
    vals: list[float] = []
    for r in records or []:
        src = getattr(r, "source", None) or "lab"
        if src not in TRAINABLE_SOURCES:
            continue
        v = (getattr(r, "measured", None) or {}).get(metric)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return None
    return min(vals) if direction == "minimize" else max(vals)


def primary_objective_spec(req: Requirement) -> ObjectiveSpec:
    """Primary objective: req.objectives[0], else a target-less default.

    注意：收敛判定只看主目标（objectives[0]）。其余目标的 target_value
    不参与收敛判定（多目标权衡走 Pareto/加权分，不走收敛门）。
    """
    from ..domain.project_spec import primary_objective

    objectives = getattr(req, "objectives", None) or []
    if objectives:
        return objectives[0]
    return ObjectiveSpec(metric=primary_objective(req))


def evaluate_convergence(
    *,
    prior_rmse_history: list[dict[str, float]] | None,
    current_rmse: dict[str, float] | None,
    records: list | None,
    objective: ObjectiveSpec,
    enabled: bool,
    eps: float,
    patience: int,
    prior_generations: list | None = None,
    current_generation: str | None = None,
) -> tuple[bool, str]:
    """统一收敛判定：目标达成（P2-4）或 RMSE 平台期。

    Returns (converged, reason); reason ∈
    {"target_achieved", "rmse_plateau", ""}.

    P2-3: ``prior_generations`` / ``current_generation`` 与 RMSE 历史
    并行的训练代际戳（见 :func:`rmse_generation_stamp`）。两者都传入
    时，平台期只在窗口内发生过重训的前提下判定；未传入时沿用旧语义。
    """
    if not enabled:
        return False, REASON_NONE
    # 仅用于 plateau 检测：近 N 轮足够，避免跨长循环无限增长内存（S7）。
    history = list(prior_rmse_history or [])[-_PLATEAU_HISTORY_CAP:]
    full_history = history + [current_rmse] if current_rmse else history
    generations: list | None = None
    if prior_generations is not None:
        generations = list(prior_generations)[-_PLATEAU_HISTORY_CAP:]
        if current_rmse:
            generations = generations + [current_generation]
    best_measured = best_objective_value(
        records, objective.metric, objective.direction
    )
    if target_achieved(best_measured, objective):
        return True, REASON_TARGET_ACHIEVED
    if current_rmse and rmse_plateau_detected(
        full_history, eps=eps, patience=patience, generations=generations
    ):
        return True, REASON_RMSE_PLATEAU
    return False, REASON_NONE
