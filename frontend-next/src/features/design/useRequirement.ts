import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import type { ProjectDetail, Requirement } from "@/features/spec/types";
import type { LeverSpec } from "./types";

export type DesignRequirement = Requirement & { levers?: LeverSpec[] };

/**
 * The current project's requirement — what the legacy store kept as ``requirement`` (domain, objectives, levers).
 * A project without one still has a domain, so the backend can reconstruct its baseline formulation.
 */
export function useRequirement() {
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId], enabled: !!projectId,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`),
  });
  const stored = project.data?.workspace.requirement;
  const requirement: DesignRequirement | null = project.data
    ? { ...(stored ?? {}), domain: stored?.domain || project.data.domain || "anticorrosion_coating", project_id: projectId ?? "" }
    : null;
  const saveLevers = useMutation({
    mutationFn: (levers: LeverSpec[]) =>
      call("PUT", `/api/projects/${projectId}`, { workspace: { requirement: { ...(requirement ?? {}), levers } } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  return { projectId, project, requirement, levers: (requirement?.levers ?? []) as LeverSpec[], saveLevers };
}
