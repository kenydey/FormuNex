/** The current project's workspace (requirement, leaderboard, last research round), read and patched presence-wise. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import type { ProjectDetail } from "@/features/spec/types";
import type { Formulation, ObjectiveSpec, ResearchResult } from "./types";

export interface Workspace {
  requirement: (Record<string, unknown> & { domain: string; objectives?: ObjectiveSpec[]; active_formulation?: Formulation | null;
    levers?: { name: string; material_map?: Record<string, string> | null }[] }) | null;
  leaderboard?: Formulation[];
  research?: ResearchResult | null;
  sources?: { identifier?: string; title: string }[];
  selected_sources?: string[];
  search_query?: string;
}

export function useWorkspace() {
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId], enabled: !!projectId,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`),
  });
  const ws = project.data?.workspace as unknown as Workspace | undefined;
  /** PUT only the given keys (the backend merges presence-based). */
  const patch = useMutation({
    mutationFn: (w: Partial<Workspace>) => call<ProjectDetail>("PUT", `/api/projects/${projectId}`, { workspace: w }),
    onSuccess: (d) => qc.setQueryData(["project", projectId], d),
  });
  return { projectId, project, ws, leaderboard: ws?.leaderboard ?? [], requirement: ws?.requirement ?? null, recommendId: ws?.research?.recommend_id ?? null, patch };
}

/** The evidence a recommend / AI-modify round is grounded on: the selected sources, else all of them. */
export function groundingSources(ws: Workspace | undefined) {
  const all = ws?.sources ?? [];
  const sel = new Set(ws?.selected_sources ?? []);
  const picked = all.filter((e) => sel.has(e.identifier || e.title));
  return picked.length ? picked : all;
}
