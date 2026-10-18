/** Pure logic of the formulation area (unit-tested in logic.test.ts). */
import type { Formulation, FormulationVersion, Ingredient, ObjectiveSpec, WorkbenchRow } from "./types";

export const OBJECTIVE_METRIC: Record<string, string> = {
  anticorrosion_coating: "salt_spray_hours",
  degreaser: "cleaning_efficiency",
  surface_treatment: "salt_spray_hours",
  autodeposition_coating: "salt_spray_hours",
};

/** Display a factor / measurement value: numbers to 3 decimals, empty as "—". */
export function fmtValue(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
}

/** Effective factor settings of a workbench row (actual overrides planned). */
export function rowFactors(r: Pick<WorkbenchRow, "planned_params" | "actual_params">): Record<string, unknown> {
  return { ...(r.planned_params || {}), ...(r.actual_params || {}) };
}

export interface DiffLine { key: string; a: unknown; b: unknown; changed: boolean }

function diffMaps(a: Record<string, unknown>, b: Record<string, unknown>): DiffLine[] {
  const keys = Array.from(new Set([...Object.keys(a), ...Object.keys(b)]));
  return keys.map((key) => ({ key, a: a[key], b: b[key], changed: a[key] !== b[key] }));
}

/** Side-by-side diff of two workbench rows: factors and measured values. */
export function buildRowDiff(a: WorkbenchRow, b: WorkbenchRow): { factors: DiffLine[]; measurements: DiffLine[] } {
  return { factors: diffMaps(rowFactors(a), rowFactors(b)), measurements: diffMaps(a.measurements || {}, b.measurements || {}) };
}

/**
 * Multi-round trend along a lineage chain. The backend returns current → root; the chart wants root → current,
 * one series per metric (null where a generation has no value).
 */
export function lineageTrend(chain: WorkbenchRow[], metrics: string[]): { rounds: string[]; series: { metric: string; data: (number | null)[] }[] } {
  const rows = [...chain].reverse();
  const keys = metrics.length ? metrics : Array.from(new Set(rows.flatMap((r) => Object.keys(r.measurements || {}))));
  return {
    rounds: rows.map((_, i) => String(i + 1)),
    series: keys.map((metric) => ({
      metric,
      data: rows.map((r) => {
        const v = Number(r.measurements?.[metric]);
        return r.measurements?.[metric] == null || r.measurements[metric] === "" || Number.isNaN(v) ? null : v;
      }),
    })),
  };
}

/** Planned vs actual factor settings (first 10 factors); ``deviated`` when both exist and differ numerically. */
export function planVsActual(row: WorkbenchRow): { key: string; planned: unknown; actual: unknown; deviated: boolean }[] {
  const keys = Array.from(new Set([...Object.keys(row.planned_params ?? {}), ...Object.keys(row.actual_params ?? {})])).slice(0, 10);
  return keys.map((key) => {
    const planned = row.planned_params?.[key];
    const actual = row.actual_params?.[key];
    return { key, planned, actual, deviated: planned != null && actual != null && Number(planned) !== Number(actual) };
  });
}

/** Human text of an objective's spec window. */
export function specText(o: ObjectiveSpec): string {
  const unit = o.unit ? ` ${o.unit}` : "";
  if (o.ref_min != null && o.ref_max != null) return `${o.ref_min}–${o.ref_max}${unit}`;
  if (o.ref_min != null) return o.direction === "minimize" ? `≤ ${o.ref_min}${unit}` : `≥ ${o.ref_min}${unit}`;
  if (o.ref_max != null) return `≤ ${o.ref_max}${unit}`;
  if (o.target_value != null) {
    const dir = o.direction === "maximize" ? "≥" : o.direction === "minimize" ? "≤" : "≈";
    return `${dir} ${o.target_value}${unit}`;
  }
  return "";
}

/** Does a measured value satisfy the spec? null when there is no value. */
export function valueMeetsSpec(o: ObjectiveSpec, value: unknown): boolean | null {
  const v = Number(value);
  if (value == null || value === "" || Number.isNaN(v)) return null;
  if (o.ref_min != null && v < o.ref_min) return false;
  if (o.ref_max != null && v > o.ref_max) return false;
  if (o.ref_min == null && o.ref_max == null && o.target_value != null) {
    if (o.direction === "maximize") return v >= o.target_value;
    if (o.direction === "minimize") return v <= o.target_value;
    return Math.abs(v - o.target_value) / Math.max(Math.abs(o.target_value), 1) <= 0.1;
  }
  return true;
}

const CAS_RE = /^(\d{2,7})-(\d{2})-(\d)$/;

export function casChecksumOk(cas: string): boolean {
  const m = CAS_RE.exec((cas || "").trim());
  if (!m) return false;
  const digits = `${m[1]}${m[2]}`;
  const sum = [...digits].reverse().reduce((s, d, i) => s + Number(d) * (i + 1), 0);
  return sum % 10 === Number(m[3]);
}

export type CasStatus = "missing" | "invalid" | "corrected" | "ok";

/** CAS state of an ingredient, taking the server's validate warnings ("<name>: …") into account. */
export function casStatus(ing: Ingredient, warnings: string[]): CasStatus {
  const labels = [ing.zh_name, ing.name].filter(Boolean) as string[];
  const mine = warnings.filter((w) => labels.some((l) => w.startsWith(`${l}:`)));
  if (mine.some((w) => w.includes("校验失败"))) return "invalid";
  if (mine.some((w) => w.includes("不一致") || w.includes("已采用"))) return "corrected";
  const cas = (ing.cas_no || "").trim();
  if (!cas) return "missing";
  return casChecksumOk(cas) ? "ok" : "invalid";
}

export function weightTotal(ings: Ingredient[]): number {
  return Math.round(ings.reduce((s, i) => s + (Number(i.weight_pct) || 0), 0) * 1e4) / 1e4;
}

export type WarningKind = "catalog" | "compliance" | "weight" | "other";
const CATALOG_RE = /CAS|SMILES|MF|目录|材料库|网络检索补全|无法被 RDKit|待重填|no CAS|missing CAS|missing MF/i;
const COMPLIANCE_RE = /SVHC|REACH|RoHS|合规/i;
const WEIGHT_RE = /weight|weights sum|weight_pct|当量比|化学计量|~100%/i;

export function classifyWarning(w: string): WarningKind {
  const s = (w || "").trim();
  if (!s) return "other";
  if (COMPLIANCE_RE.test(s)) return "compliance";
  if (CATALOG_RE.test(s)) return "catalog";
  if (WEIGHT_RE.test(s)) return "weight";
  return "other";
}

/** Which banner actions the validate warnings call for. */
export function warningActions(warnings: string[]): { proposeMissing: boolean; openMaterials: boolean; hasCompliance: boolean } {
  const kinds = warnings.map(classifyWarning);
  return { proposeMissing: kinds.includes("catalog"), openMaterials: kinds.length > 0, hasCompliance: kinds.includes("compliance") };
}

/** CSV cell with quoting and spreadsheet-formula injection guard. */
export function csvCell(v: unknown): string {
  const c = v == null ? "" : String(v);
  let s = /[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c;
  if (/^[=+\-@]/.test(s)) s = `'${s}`;
  return s;
}

export function formulaToCsv(f: Formulation): string {
  const lines = [`# Formulation,${csvCell(f.name)}`, `# Domain,${f.domain}`];
  if (f.score != null) lines.push(`# Score,${f.score}`);
  lines.push("", "Ingredient,Role,CAS,Weight %");
  for (const i of f.ingredients) lines.push([i.name, i.role, i.cas_no ?? "", i.weight_pct].map(csvCell).join(","));
  lines.push("", "Predicted property,Value,Std");
  for (const [k, v] of Object.entries(f.predicted ?? {})) lines.push([k, v, f.predicted_std?.[k] ?? ""].map(csvCell).join(","));
  return lines.join("\n");
}

export function leaderboardToCsv(forms: Formulation[]): string {
  const lines = ["Rank,Name,Domain,Score,Primary metric,Primary value,Cost CNY/kg,VOC g/L,Ingredient count,Warnings"];
  forms.forEach((f, i) => {
    const pk = OBJECTIVE_METRIC[f.domain] ?? "";
    lines.push([i + 1, f.name, f.domain, f.score ?? "", pk, pk ? f.predicted?.[pk] ?? "" : "", f.predicted?.cost_cny_per_kg ?? "",
      f.predicted?.voc_gpl ?? "", f.ingredients.length, (f.warnings ?? []).join("; ")].map(csvCell).join(","));
  });
  return lines.join("\n");
}

/** Keep an index pointing at the same card after another card was removed. */
export function shiftAfterRemove(cur: number | null, removed: number): number | null {
  if (cur == null || cur === removed) return null;
  return cur > removed ? cur - 1 : cur;
}

/** Adopt snapshot's lever → {level: material} map, used server-side to match renamed materials. */
export function materialMapOf(levers: { name: string; material_map?: Record<string, string> | null }[] | undefined) {
  const m: Record<string, Record<string, string>> = {};
  for (const l of levers ?? []) if (l.material_map && Object.keys(l.material_map).length) m[l.name] = l.material_map;
  return Object.keys(m).length ? m : undefined;
}

export interface TreeNode { name: string; value: string; children: TreeNode[]; summary: string }

/** Version list → ECharts ``tree`` roots (a version whose parent is missing starts its own root). */
export function versionTree(versions: FormulationVersion[]): TreeNode[] {
  const ids = new Set(versions.map((v) => v.id));
  const nodes = new Map<string, TreeNode>(versions.map((v) => [v.id, { name: `v${v.version}`, value: v.id, children: [], summary: v.change_summary }]));
  const roots: TreeNode[] = [];
  for (const v of versions) {
    const n = nodes.get(v.id)!;
    if (v.parent_version_id && ids.has(v.parent_version_id) && v.parent_version_id !== v.id) nodes.get(v.parent_version_id)!.children.push(n);
    else roots.push(n);
  }
  return roots;
}

/** The revision a version should be diffed against: its parent, else the previous version number. */
export function previousVersion(versions: FormulationVersion[], v: FormulationVersion): FormulationVersion | undefined {
  return (v.parent_version_id ? versions.find((x) => x.id === v.parent_version_id) : undefined)
    ?? versions.find((x) => x.version === v.version - 1);
}

/** Outcome of POST /api/experiments/{id}/sync-datalab (fail-open: nothing synced + errors = Datalab unreachable). */
export function datalabSyncOutcome(r: { synced?: number; errors?: unknown[] }): { skipped: boolean; synced: number; errors: number } {
  const synced = r.synced ?? 0;
  const errors = Array.isArray(r.errors) ? r.errors.length : 0;
  return { skipped: synced === 0 && errors > 0, synced, errors };
}

/** Top-level keys a DataLab DeepDiff touches (e.g. "values_changed"), flattened to the changed paths when present. */
export function deepDiffSummary(diff: Record<string, unknown> | undefined | null): string[] {
  if (!diff) return [];
  const out: string[] = [];
  for (const [kind, body] of Object.entries(diff)) {
    if (body && typeof body === "object" && !Array.isArray(body)) out.push(...Object.keys(body).map((p) => `${kind}: ${p}`));
    else if (Array.isArray(body)) out.push(...body.map((p) => `${kind}: ${String(p)}`));
    else out.push(kind);
  }
  return out;
}

/** A recommended / leaderboard formulation → rows of the formulation editor grid. */
export function ingredientsToEditorRows(ings: Ingredient[]): { name: string; zh_name?: string; cas_no?: string; role?: string; weight_pct: number }[] {
  return ings.map((i) => ({ name: i.name, zh_name: i.zh_name ?? undefined, cas_no: i.cas_no ?? undefined, role: i.role || undefined, weight_pct: Number(i.weight_pct) || 0 }));
}

/** Pareto front (indices) for two objectives. */
export function paretoFront(points: { x: number; y: number }[], xDir: "min" | "max", yDir: "min" | "max"): number[] {
  const better = (a: number, b: number, d: "min" | "max") => (d === "min" ? a <= b : a >= b);
  const strictly = (a: number, b: number, d: "min" | "max") => (d === "min" ? a < b : a > b);
  return points.flatMap((p, i) =>
    points.some((q, j) => j !== i && better(q.x, p.x, xDir) && better(q.y, p.y, yDir) && (strictly(q.x, p.x, xDir) || strictly(q.y, p.y, yDir))) ? [] : [i]);
}
