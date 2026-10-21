/** 主路径向导 (legacy PathWizard): three guided journeys deep-linking existing pages. */

export type PathId = "formula" | "substitute" | "knowledge";

export interface PathStep {
  id: string;
  /** Candidate routes in order of preference; the first one that exists in the navigation wins. */
  routes: string[];
  /** Needs the current project (its id replaces ``:projectId``). */
  needsProject?: boolean;
}

export interface PathDef { id: PathId; maturity: "stable" | "beta"; steps: PathStep[]; needsProject?: boolean }

export const PATHS: PathDef[] = [
  { id: "formula", maturity: "stable", steps: [
    { id: "requirement", routes: ["/spec/projects/:projectId", "/spec/projects"] },
    { id: "recommend", routes: ["/formulation/recommend", "/lab/recommend", "/lab/formulations"] },
    { id: "doe", routes: ["/lab/doe"] },
    { id: "ledger", routes: ["/cockpit/workbench", "/lab/tasks"] },
    { id: "loop", routes: ["/cockpit/optimization?tab=loop", "/cockpit/optimization?tab=pareto"] },
  ] },
  { id: "substitute", maturity: "stable", steps: [
    { id: "materials", routes: ["/materials/raw"] },
    { id: "substitute", routes: ["/lab/substitution", "/materials/raw?tab=structure"] },
    { id: "ledger", routes: ["/cockpit/workbench", "/lab/tasks"] },
  ] },
  { id: "knowledge", maturity: "beta", needsProject: true, steps: [
    { id: "sources", routes: ["/spec/projects/:projectId/sources"], needsProject: true },
    { id: "wiki", routes: ["/knowledge/wiki", "/wiki"] },
    { id: "quality", routes: ["/knowledge/quality", "/wiki/quality"] },
    { id: "reports", routes: ["/knowledge/research/artifacts"] },
  ] },
];

/**
 * Resolve a step to a concrete link: substitute the project id and keep only routes the app knows
 * (``known`` = navigation paths; parametrised project routes are always known). ``null`` = not available.
 */
export function resolveStep(step: PathStep, known: Set<string>, projectId: string | null): string | null {
  for (const r of step.routes) {
    if (r.includes(":projectId")) {
      if (!projectId) continue;
      return r.replace(":projectId", encodeURIComponent(projectId));
    }
    const base = r.split("?")[0];
    if (known.has(base)) return r;
  }
  return null;
}

export function flattenPaths(items: { path: string; children?: { path: string }[] }[]): Set<string> {
  const out = new Set<string>();
  const walk = (xs: { path: string; children?: { path: string }[] }[]) => {
    for (const x of xs) { out.add(x.path); if (x.children) walk(x.children); }
  };
  walk(items);
  return out;
}
