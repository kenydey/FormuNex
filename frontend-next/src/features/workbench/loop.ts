/**
 * Closed-loop iteration (legacy ``workflowSlice.runLoop / followLoopTask / adoptDoePlanToWorkbench``): POST
 * /api/loop/iterate → background task → LoopReport, persisted into the project workspace so the cockpit shows
 * the same state after a reload. Run state lives in a small store so a running loop survives page changes.
 */
import type { QueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { call } from "@/api/client";
import { awaitTask, cancelTask, type AsyncTaskAccepted, type TaskProgressEvent } from "@/api/stream";
import type { ProjectDetail, Requirement } from "@/features/spec/types";
import { strategyForPlan } from "./ledger";
import type { CampaignDetail, DoePlan, LoopReport, LoopWorkspace } from "./types";

/** Request body of POST /api/loop/iterate (``LoopRequest`` = requirement + loop options). */
export function loopBody(req: Requirement, ws: LoopWorkspace, campaignId: number | null | undefined) {
  return {
    ...req,
    optimize_iterations: 24,
    n_suggest: 4,
    optimize_engine: ws.optimize_engine || "auto",
    doe_engine: ws.loop_doe_engine || "auto",
    workbench_campaign_id: campaignId ?? null,
    campaign_state: ws.campaign_state ?? null,
    prior_rmse_history: ws.rmse_history ?? [],
    prior_optimization: ws.loop_report?.optimization ?? null,
    prior_next_doe: ws.loop_report?.next_doe ?? ws.doe_plan ?? null,
    budget_remaining: null,
  };
}

/** Converged with nothing new: keep the previous leaderboard / next DOE. */
export const loopSkipsReplace = (r: LoopReport) => r.converged && (r.optimization?.top_formulations?.length ?? 0) === 0;

/** Workspace keys written after a loop report (legacy ``applyLoopReportToDraft``). */
export function workspaceAfterLoop(ws: LoopWorkspace, r: LoopReport): Record<string, unknown> {
  const { model_info, rmse_by_metric, optimization, next_doe, engine, campaign_state } = r;
  const adaptive = { strategy_label: r.strategy_label, strategy_rationale: r.strategy_rationale, run_explanations: r.run_explanations ?? [],
    anomalies: r.anomalies ?? [], recommended_next_action: r.recommended_next_action, budget_remaining: r.budget_remaining ?? null };
  const out: Record<string, unknown> = {
    loop_report: r,
    rmse_history: [...(ws.rmse_history ?? []), rmse_by_metric ?? {}],
    models: model_info ?? [],
    last_al_engine: engine,
    adaptive_doe: adaptive,
  };
  if (campaign_state) out.campaign_state = campaign_state;
  if (!loopSkipsReplace(r)) {
    out.leaderboard = optimization.top_formulations;
    out.optimization_history = optimization.history;
    out.doe_plan = next_doe;
    out.measured = {};
  }
  return out;
}

async function freshProject(projectId: string) {
  return call<ProjectDetail>("GET", `/api/projects/${projectId}`);
}

async function patchWs(qc: QueryClient, projectId: string, ws: Record<string, unknown>) {
  const d = await call<ProjectDetail>("PUT", `/api/projects/${projectId}`, { workspace: ws });
  qc.setQueryData(["project", projectId], d);
  return d;
}

const requirementOf = (p: ProjectDetail): Requirement =>
  ({ ...(p.workspace.requirement ?? { domain: p.domain || "anticorrosion_coating", objectives: [] }), project_id: p.id });

/**
 * Create a workbench campaign from a DOE plan for the project (and mark the plan active). Returns the campaign
 * and an optional warning when the plan status could not be synced (the campaign still exists).
 */
export async function adoptPlan(qc: QueryClient, projectId: string | null, plan: DoePlan, opts: { name?: string; engineHint?: string | null } = {}) {
  const project = projectId ? await freshProject(projectId) : null;
  const ws = (project?.workspace ?? {}) as LoopWorkspace & { last_al_engine?: string | null };
  const wb = await call<CampaignDetail>("POST", "/api/experiments/workbench/campaigns", {
    plan, name: opts.name || undefined, strategy: strategyForPlan(plan, opts.engineHint ?? ws.last_al_engine ?? ws.loop_report?.engine ?? null),
    requirement: project ? requirementOf(project) : undefined, project_id: projectId ?? undefined,
  });
  let warning: string | null = null;
  if (plan.plan_id) {
    try {
      await call("POST", `/api/doe/${plan.plan_id}/activate`, {});
    } catch (e) {
      warning = e instanceof Error ? e.message : String(e);
    }
  }
  if (projectId) {
    await patchWs(qc, projectId, { doe_plan: plan, measured: {}, workbench_campaign_id: wb.campaign_id,
      workbench_adopted_plan_id: plan.plan_id || ws.workbench_adopted_plan_id || null, workbench_objectives_snapshot: wb.objectives_snapshot ?? [] });
  }
  void qc.invalidateQueries({ queryKey: ["workbench", "campaigns"] });
  return { campaign: wb, warning };
}

interface LoopRunState {
  running: boolean;
  taskId: string | null;
  event: TaskProgressEvent | null;
  error: string | null;
  retry: boolean;
  lastReport: LoopReport | null;
}

export const useLoopRun = create<LoopRunState>()(() => ({ running: false, taskId: null, event: null, error: null, retry: false, lastReport: null }));

const isCancel = (msg: string) => /取消|cancel/i.test(msg);

/** Follow an already-started loop task (e.g. one dispatched by a workbench sync). */
export async function followLoop(qc: QueryClient, projectId: string | null, taskId: string, campaignId?: number | null): Promise<void> {
  useLoopRun.setState({ running: true, taskId, event: null, error: null, retry: false });
  try {
    const ev = await awaitTask(taskId, { onEvent: (e) => useLoopRun.setState({ event: e }) });
    const report = (ev.data ?? null) as unknown as LoopReport | null;
    if (!report || !report.next_doe) {
      useLoopRun.setState({ running: false });
      return;
    }
    useLoopRun.setState({ lastReport: report });
    if (projectId) {
      const project = await freshProject(projectId);
      const ws = project.workspace as LoopWorkspace;
      await patchWs(qc, projectId, workspaceAfterLoop(ws, report));
      if (ws.auto_adopt_next_doe_on_loop && !loopSkipsReplace(report) && report.next_doe.plan_id) {
        await adoptPlan(qc, projectId, report.next_doe, { engineHint: report.engine });
      }
      const max = ws.auto_loop_max_rounds ?? 5;
      const round = ws.auto_loop_round ?? 0;
      if (ws.auto_loop_on_sync && !report.converged && round < max - 1) {
        await patchWs(qc, projectId, { auto_loop_round: round + 1 });
        useLoopRun.setState({ running: false });
        setTimeout(() => void runLoop(qc, projectId, campaignId ?? ws.workbench_campaign_id ?? null), 800);
        return;
      }
      if (ws.auto_loop_on_sync && report.converged) await patchWs(qc, projectId, { auto_loop_round: 0 });
    }
    void qc.invalidateQueries({ queryKey: ["workbench"] });
    void qc.invalidateQueries({ queryKey: ["models"] });
    void qc.invalidateQueries({ queryKey: ["training-status"] });
    useLoopRun.setState({ running: false });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    useLoopRun.setState({ running: false, error: msg, retry: !isCancel(msg) });
  }
}

/** Start one loop round for the project (requirement + loop options from its workspace). */
export async function runLoop(qc: QueryClient, projectId: string, campaignId: number | null): Promise<void> {
  useLoopRun.setState({ running: true, error: null, retry: false, event: null });
  try {
    const project = await freshProject(projectId);
    const ws = project.workspace as LoopWorkspace;
    const accepted = await call<AsyncTaskAccepted>("POST", "/api/loop/iterate", loopBody(requirementOf(project), ws, campaignId));
    await followLoop(qc, projectId, accepted.task_id, campaignId);
  } catch (e) {
    useLoopRun.setState({ running: false, error: e instanceof Error ? e.message : String(e), retry: true });
  }
}

export async function cancelLoop(): Promise<void> {
  const id = useLoopRun.getState().taskId;
  if (id) await cancelTask(id);
}
