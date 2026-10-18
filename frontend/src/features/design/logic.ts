import type {
  DesignCandidate, FactorCandidate, Formulation, HardConstraint, LeverSpec, ObjectiveSpec, QCMeasurementView, SurechemblCandidate,
} from "./types";

// ── Levers (LeversEditor) ────────────────────────────────────────────────────

/** Flat, string-typed edit row of a lever (levels and material map as comma-separated text). */
export interface LeverRow {
  name: string;
  low: number;
  high: number;
  unit: string;
  kind: "continuous" | "discrete";
  levels: string;
  material_map: string;
}

/** "a, 1, 2.5" → ["a", 1, 2.5]: numeric-looking levels become numbers (legacy LeversEditor behaviour). */
export function parseLevels(text: string): Array<number | string> {
  return text.split(/[,，]/).map((s) => s.trim()).filter((s) => s.length > 0)
    .map((s) => (Number.isNaN(Number(s)) ? s : Number(s)));
}

/** "树脂B=聚氨酯树脂X, C=Y" → {树脂B: "聚氨酯树脂X", C: "Y"}; null when empty. */
export function parseMaterialMap(text: string): Record<string, string> | null {
  const map: Record<string, string> = {};
  for (const pair of text.split(/[,，]/).map((s) => s.trim()).filter(Boolean)) {
    const eq = pair.indexOf("=");
    if (eq <= 0) continue;
    const k = pair.slice(0, eq).trim();
    const v = pair.slice(eq + 1).trim();
    if (k && v) map[k] = v;
  }
  return Object.keys(map).length ? map : null;
}

export const formatMaterialMap = (m: Record<string, string> | null | undefined) =>
  Object.entries(m ?? {}).map(([k, v]) => `${k}=${v}`).join(", ");

export function leverToRow(l: LeverSpec): LeverRow {
  return {
    name: l.name, low: l.low, high: l.high, unit: l.unit ?? "wt%",
    kind: l.kind === "discrete" ? "discrete" : "continuous",
    levels: (l.levels ?? []).join(", "), material_map: formatMaterialMap(l.material_map),
  };
}

/** Back to the backend shape: continuous levers carry no kind/levels; discrete ones need ≥ 2 levels. */
export function rowToLever(r: LeverRow): LeverSpec {
  const base: LeverSpec = { name: (r.name ?? "").trim(), low: Number(r.low) || 0, high: Number(r.high) || 0, unit: r.unit || "wt%" };
  if (r.kind !== "discrete") return base;
  const levels = parseLevels(r.levels ?? "");
  return { ...base, kind: "discrete", levels: levels.length >= 2 ? levels : ["A", "B"], material_map: parseMaterialMap(r.material_map ?? "") };
}

/** Problems that would make the backend reject the levers (empty name, low > high, too few discrete levels, duplicates). */
export function leverProblems(rows: LeverRow[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  rows.forEach((r, i) => {
    const name = (r.name ?? "").trim();
    if (!name) out.push(`#${i + 1}: name`);
    else if (seen.has(name)) out.push(`#${i + 1}: duplicate ${name}`);
    seen.add(name);
    if (r.kind !== "discrete" && Number(r.low) > Number(r.high)) out.push(`#${i + 1}: low > high`);
    if (r.kind === "discrete" && parseLevels(r.levels ?? "").length < 2) out.push(`#${i + 1}: levels < 2`);
  });
  return out;
}

/** AI factor suggestions → levers (discrete hints keep their levels). */
export function factorHintsToLevers(hints: FactorCandidate[]): LeverSpec[] {
  return hints.map((f) => ({
    name: f.name, low: f.low, high: f.high, unit: f.unit || "wt%",
    ...(f.kind === "discrete" && f.levels && f.levels.length >= 2 ? { kind: "discrete", levels: f.levels } : {}),
  }));
}

// ── Classic DOE ──────────────────────────────────────────────────────────────

export type DoeEngine = "auto" | "native" | "pydoe";
export const NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"] as const;
export const PYDOE_DESIGNS = ["bbdesign", "simplex_lattice", "sobol"] as const;
export const AI_DESIGN = "ai_active";

/** Which designs an engine offers (legacy DoeResultsPanel): pydoe has no full factorial etc. */
export function designsForEngine(engine: DoeEngine): string[] {
  if (engine === "pydoe") return ["ccd", "lhs", ...PYDOE_DESIGNS, AI_DESIGN];
  if (engine === "native") return [...NATIVE_DESIGNS, AI_DESIGN];
  return [...NATIVE_DESIGNS, ...PYDOE_DESIGNS, AI_DESIGN];
}

/** Workbench strategy label a plan is seeded with. */
export const strategyForPlan = (design: string, alEngine?: string | null) =>
  design === AI_DESIGN ? `BayBE-${alEngine || "loop"}-next` : `DOE-${design}`;

/** Allowed DOE plan lifecycle actions (backend _PLAN_TRANSITIONS: draft→active|aborted, active→completed|aborted). */
export function planActions(status: string | undefined): Array<"activate" | "complete" | "abort"> {
  const s = status || "draft";
  if (s === "draft") return ["activate", "abort"];
  if (s === "active") return ["complete", "abort"];
  return [];
}

// ── Inverse design ───────────────────────────────────────────────────────────

export function defaultConstraints(domain: string): HardConstraint[] {
  if (domain === "degreaser") return [{ metric: "cleaning_efficiency", op: "ge", value: 90 }];
  return [
    { metric: "salt_spray_hours", op: "ge", value: 1000 },
    { metric: "voc_gpl", op: "le", value: 250 },
    { metric: "cost_cny_per_kg", op: "le", value: 40 },
  ];
}

export function defaultObjectives(domain: string): ObjectiveSpec[] {
  const primary = domain === "degreaser" ? "cleaning_efficiency" : "salt_spray_hours";
  return [{ metric: primary, weight: 0.5, direction: "maximize" }, { metric: "cost_cny_per_kg", weight: 0.5, direction: "minimize" }];
}

/** Number of distinct ingredient sets among the candidates. */
export const distinctCompositions = (cands: DesignCandidate[]) => new Set(cands.map((c) => [...c.materials].sort().join("|"))).size;

/** The candidates to adopt: the Pareto front, or the top 5 when no front was computed. */
export function frontier(cands: DesignCandidate[]): DesignCandidate[] {
  const front = cands.filter((c) => c.pareto_rank === 0);
  return front.length ? front : cands.slice(0, 5);
}

export function primaryMetric(p: Record<string, number> | undefined): number | undefined {
  return p?.salt_spray_hours ?? p?.cleaning_efficiency ?? p?.coating_weight_gsm;
}

// ── Substitution / similarity / QC ───────────────────────────────────────────

/** Signed % delta display: null → "—", |pct| < 0.05 → "flat". */
export function deltaDisplay(pct: number | null | undefined): { text: string; tone: "none" | "flat" | "up" | "down" } {
  if (pct == null) return { text: "—", tone: "none" };
  if (Math.abs(pct) < 0.05) return { text: "", tone: "flat" };
  return { text: `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`, tone: pct > 0 ? "up" : "down" };
}

export const supplyBadges = (c: { supply_badges?: string[]; stale_price?: boolean; missing_price?: boolean; long_lead_time?: boolean }) =>
  c.supply_badges?.length ? c.supply_badges
    : [c.stale_price && "stale_price", c.missing_price && "missing_price", c.long_lead_time && "long_lead_time"].filter(Boolean) as string[];

export function surechemblRef(row: SurechemblCandidate): string {
  const patents = (row.patents ?? []).map((p) => p.doc_id).filter(Boolean).slice(0, 3);
  return [row.chemical_id ? `SCHEMBL:${row.chemical_id}` : null, patents.length ? `patents:${patents.join(",")}` : null].filter(Boolean).join(" ");
}

/** Material names listed by a 404 "material not in formulation" error (detail.candidates). */
export function notFoundCandidates(detail: unknown): string[] {
  if (detail && typeof detail === "object" && "candidates" in detail) {
    const c = (detail as { candidates?: unknown }).candidates;
    if (Array.isArray(c)) return c.filter((x): x is string => typeof x === "string" && !!x);
  }
  return [];
}

/**
 * The ``{ingredient: wt%}`` map the similarity search compares — explicit ``factors`` win, else the ingredients' wt%
 * (summed per name; zero / non-finite entries dropped).
 */
export function similarityFactors(f: Pick<Formulation, "factors" | "ingredients">): Record<string, number> {
  const explicit = Object.entries(f.factors ?? {}).filter(([, v]) => typeof v === "number");
  if (explicit.length) return Object.fromEntries(explicit);
  const out: Record<string, number> = {};
  for (const ing of f.ingredients ?? []) {
    if (ing.name && Number.isFinite(ing.weight_pct) && ing.weight_pct > 0) out[ing.name] = (out[ing.name] ?? 0) + ing.weight_pct;
  }
  return out;
}

export function specRange(m: Pick<QCMeasurementView, "spec_min" | "spec_max">): string {
  if (m.spec_min != null && m.spec_max != null) return `${m.spec_min} ~ ${m.spec_max}`;
  if (m.spec_min != null) return `≥ ${m.spec_min}`;
  if (m.spec_max != null) return `≤ ${m.spec_max}`;
  return "—";
}
