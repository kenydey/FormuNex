import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";

export interface Requirement { domain: string; [k: string]: unknown }
interface ProjectLite { id: string; domain: string; workspace: { requirement: Requirement | null; notebooklm_notebook_id?: string | null; [k: string]: unknown } }

/** The current project's R&D requirement (shell header project), used to focus search and deep research. */
export function useProjectRequirement() {
  const projectId = useUi((s) => s.projectId);
  const project = useQuery({
    queryKey: ["project", projectId], enabled: !!projectId,
    queryFn: () => call<ProjectLite>("GET", `/api/projects/${projectId}`),
  });
  const requirement = project.data?.workspace?.requirement ?? null;
  const domain = requirement?.domain || project.data?.domain || "anticorrosion_coating";
  // P1-7: the project's NotebookLM binding (undefined while loading, null
  // when unbound) — the single source external search prefills from.
  const notebookId = project.data ? (project.data.workspace?.notebooklm_notebook_id ?? null) : undefined;
  return { projectId, requirement, domain, notebookId };
}

/** Product-domain options of the ``ProductDomain`` enum. */
export function useDomainOptions() {
  const { t } = useTranslation();
  return [
    { value: "anticorrosion_coating", label: t("research.domain.anticorrosion_coating", "防腐蚀涂料") },
    { value: "degreaser", label: t("research.domain.degreaser", "脱脂剂") },
    { value: "surface_treatment", label: t("research.domain.surface_treatment", "表面处理剂") },
    { value: "autodeposition_coating", label: t("research.domain.autodeposition_coating", "自沉积涂料") },
  ];
}
