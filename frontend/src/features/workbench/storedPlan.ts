/** Stored DOE plans (``doe_plans``): read one by id so a ledger can be created from any plan, not just the project's current one. */
import { call } from "@/api/client";
import type { DoeFactor, DoePlan, DoeRun } from "./types";

/** GET /api/doe/plans/{plan_id} — the /api/doe/history item shape plus ``project_id``. */
export interface StoredPlan {
  plan_id: string;
  design: string;
  domain?: string | null;
  factors?: DoeFactor[];
  runs?: DoeRun[];
  notes?: string;
  status?: string;
  campaign_id?: number | null;
  round?: number | null;
  created_at?: string | null;
  project_id?: string | null;
}

export const fetchStoredPlan = (planId: string, projectId?: string | null) =>
  call<StoredPlan>("GET", `/api/doe/plans/${encodeURIComponent(planId)}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`);

/** The ``DOEPlan`` body POST /api/experiments/workbench/campaigns expects. */
export function toDoePlan(s: StoredPlan): DoePlan {
  return {
    design: s.design, factors: s.factors ?? [], runs: s.runs ?? [], notes: s.notes ?? "", plan_id: s.plan_id,
    ...(s.domain ? { domain: s.domain } : {}), status: s.status ?? "draft",
  };
}

/** Option label of a stored plan in pickers: design · runs · date · short id. */
export function storedPlanLabel(s: Pick<StoredPlan, "plan_id" | "design" | "runs" | "created_at" | "round">): string {
  const parts = [s.design, `n=${s.runs?.length ?? 0}`];
  if (s.round != null) parts.push(`R${s.round}`);
  if (s.created_at) parts.push(s.created_at.slice(0, 16).replace("T", " "));
  return `${parts.join(" · ")} · ${s.plan_id.slice(0, 8)}`;
}
