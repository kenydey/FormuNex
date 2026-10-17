import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import type { ProjectDetail } from "@/features/spec/types";
import type { CampaignSummary, LoopWorkspace, TrainingStatus } from "./types";
import { saveText as sharedSaveText } from "@/shared/saveFile";

/** GET /api/meta engine probes; an engine whose probe has not loaded counts as available (legacy ``useEngineAvailability``). */
export function useEngines() {
  const q = useQuery({
    queryKey: ["meta"], staleTime: 5 * 60_000,
    queryFn: () => call<{ designs?: string[]; engines?: Record<string, { available?: boolean; label?: string }> }>("GET", "/api/meta"),
  });
  const map: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(q.data?.engines ?? {})) map[k] = Boolean(v?.available);
  return { map, designs: q.data?.designs ?? [], ok: (id: string) => engineOk(map, id) };
}

export function engineOk(map: Record<string, boolean>, id: string): boolean {
  return !(id in map) || map[id] !== false;
}

/** The current project (header selection) with its workspace; ``patch`` merges keys into the workspace. */
export function useProjectWorkspace() {
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const project = useQuery({ queryKey: ["project", projectId], enabled: !!projectId,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`) });
  const patch = useMutation({
    mutationFn: (ws: Partial<LoopWorkspace> & Record<string, unknown>) => call<ProjectDetail>("PUT", `/api/projects/${projectId}`, { workspace: ws }),
    onSuccess: (d) => qc.setQueryData(["project", projectId], d),
  });
  const ws = (project.data?.workspace ?? {}) as LoopWorkspace;
  return { projectId, project, ws, patch };
}

/** Workbench campaigns of the current project (unassigned campaigns are listed too, as in legacy). */
export function useCampaigns() {
  const projectId = useUi((s) => s.projectId);
  const q = useQuery({ queryKey: ["workbench", "campaigns"], queryFn: () => call<CampaignSummary[]>("GET", "/api/experiments/workbench/campaigns") });
  const items = (q.data ?? []).filter((c) => !projectId || !c.project_id || c.project_id === projectId);
  return { ...q, items };
}

export function useTrainingStatus() {
  return useQuery({ queryKey: ["training-status"], queryFn: () => call<TrainingStatus>("GET", "/api/training-status") });
}

/** First-packet wording while a model is cold-starting (legacy ``coldStartMessage``). */
export function coldStartMessage(stage: string | undefined, message: string | undefined, fallback: string, coldText: string): string {
  if (message) return message;
  if (stage === "retrieve") return coldText;
  return fallback;
}

/** Save in-memory text (browser download / desktop save dialog). */
export function saveText(text: string, filename: string, type = "text/csv;charset=utf-8") {
  void sharedSaveText(text, filename, type);
}
