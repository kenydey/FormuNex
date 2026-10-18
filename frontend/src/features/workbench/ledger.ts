/**
 * Pure ledger helpers for the campaign workbench (ported from legacy ``utils/workbenchColumns.ts`` and the
 * save / batch / export logic of ``LabWorkbench``).
 */
import type { Change } from "@/shared/StructuredDiff";
import type { DoePlan, ObjectiveSpec, WorkbenchRow } from "./types";

export const STATUS_OPTIONS = ["Pending", "In Progress", "Completed", "Blocked"] as const;
export const RATING_OPTIONS = ["A", "B", "C", "D", "pass", "fail"];

/** Excel-friendly numeric parse: blanks → null, thousands separators / spaces stripped, junk → null. */
export function parseNumeric(raw: unknown): number | null {
  if (raw == null) return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  const s = String(raw).trim().replace(/\s+/g, "").replace(/,(?=\d{3}(\D|$))/g, "");
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

export function isRatingObjective(obj: ObjectiveSpec): boolean {
  return obj.value_type === "rating" || /rating|grade|level/i.test(obj.metric);
}

/** Spec window text: ≥800 h / 8–15 g/m² / ≈ 12.5. */
export function specText(obj: ObjectiveSpec): string {
  const unit = obj.unit ? ` ${obj.unit}` : "";
  if (obj.ref_min != null && obj.ref_max != null) return `${obj.ref_min}–${obj.ref_max}${unit}`;
  if (obj.ref_min != null) return obj.direction === "minimize" ? `≤ ${obj.ref_min}${unit}` : `≥ ${obj.ref_min}${unit}`;
  if (obj.ref_max != null) return `≤ ${obj.ref_max}${unit}`;
  if (obj.target_value != null) {
    const dir = obj.direction === "maximize" ? "≥" : obj.direction === "minimize" ? "≤" : "≈";
    return `${dir} ${obj.target_value}${unit}`;
  }
  return "";
}

/** Whether a value meets the objective's spec (null = empty / non-numeric). */
export function valueMeetsSpec(obj: ObjectiveSpec, value: unknown): boolean | null {
  if (value == null || value === "") return null;
  const v = Number(value);
  if (Number.isNaN(v)) return null;
  if (obj.ref_min != null && v < obj.ref_min) return false;
  if (obj.ref_max != null && v > obj.ref_max) return false;
  if (obj.ref_min == null && obj.ref_max == null && obj.target_value != null) {
    if (obj.direction === "maximize") return v >= obj.target_value;
    if (obj.direction === "minimize") return v <= obj.target_value;
    return Math.abs(v - obj.target_value) / Math.max(Math.abs(obj.target_value), 1) <= 0.1;
  }
  return true;
}

/** Factor columns: the DOE plan's factors, else the planned-parameter keys of the rows. */
export function factorKeys(plan: Pick<DoePlan, "factors"> | null | undefined, rows: WorkbenchRow[]): string[] {
  if (plan?.factors?.length) return plan.factors.map((f) => f.name);
  const keys: string[] = [];
  for (const r of rows) for (const k of Object.keys(r.planned_params ?? {})) if (!keys.includes(k)) keys.push(k);
  return keys;
}

/** Frozen campaign snapshot wins, then the project requirement, then the legacy default metric. */
export function effectiveObjectives(requirement: ObjectiveSpec[] | undefined | null, snapshot: ObjectiveSpec[] | undefined | null): ObjectiveSpec[] {
  if (snapshot && snapshot.length) return snapshot;
  if (requirement && requirement.length) return requirement;
  return [{ metric: "salt_spray_hours", weight: 1, direction: "maximize" }];
}

export const hasMeasurement = (r: WorkbenchRow) => Object.values(r.measurements ?? {}).some((v) => v != null && v !== "");

export function ledgerStats(rows: WorkbenchRow[]) {
  return {
    total: rows.length,
    completed: rows.filter((r) => r.status === "Completed").length,
    measured: rows.filter(hasMeasurement).length,
    ingested: rows.filter((r) => r.ingested).length,
  };
}

/** Rows as PUT /api/experiments/workbench/sync expects them: only objective metrics, finite actual params. */
export function buildSyncRows(rows: WorkbenchRow[], metrics: string[]) {
  return rows.map((r) => {
    const measurements: Record<string, number | string> = {};
    for (const m of metrics) {
      const v = r.measurements?.[m];
      if (v !== undefined && v !== null && v !== "") measurements[m] = v;
    }
    const actual_params = Object.fromEntries(
      Object.entries(r.actual_params ?? {}).filter(([, v]) => typeof v === "number" && Number.isFinite(v)),
    ) as Record<string, number>;
    return { id: r.id, status: r.status, actual_params, measurements, note: r.note ?? null, tags: r.tags ?? [] };
  });
}

/** Editable column ids: ``status``, ``actual:<factor>``, ``meas:<metric>``. */
export type ColumnId = string;

export function editableColumns(factors: string[], objectives: ObjectiveSpec[]): ColumnId[] {
  return ["status", ...factors.map((f) => `actual:${f}`), ...objectives.map((o) => `meas:${o.metric}`)];
}

/** Set one cell (returns a new row). Ratings stay text; other measurements / params are parsed as numbers. */
export function setCell(row: WorkbenchRow, col: ColumnId, raw: unknown, objectives: ObjectiveSpec[] = []): WorkbenchRow {
  if (col === "status") {
    const s = String(raw ?? "").trim();
    const match = STATUS_OPTIONS.find((o) => o.toLowerCase() === s.toLowerCase());
    return match ? { ...row, status: match } : row;
  }
  const [kind, key] = [col.slice(0, col.indexOf(":")), col.slice(col.indexOf(":") + 1)];
  if (kind === "actual") return { ...row, actual_params: { ...row.actual_params, [key]: parseNumeric(raw) } };
  if (kind === "meas") {
    const obj = objectives.find((o) => o.metric === key);
    const text = raw == null ? "" : String(raw).trim();
    const value = obj && isRatingObjective(obj) ? (text === "" ? null : text) : parseNumeric(raw);
    return { ...row, measurements: { ...row.measurements, [key]: value } };
  }
  return row;
}

/** Split clipboard text copied from Excel / WPS (tab-separated, one line per row). */
export function parseClipboard(text: string): string[][] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  return lines.map((l) => l.split("\t"));
}

/**
 * Paste a block of cells starting at (startRow, startCol) — like pasting into a spreadsheet. Cells beyond the
 * table are ignored. Returns the new rows and the ids of rows that changed.
 */
export function applyPaste(rows: WorkbenchRow[], cells: string[][], startRow: number, startCol: number, columns: ColumnId[],
  objectives: ObjectiveSpec[] = []): { rows: WorkbenchRow[]; changed: number[] } {
  const next = [...rows];
  const changed: number[] = [];
  cells.forEach((line, i) => {
    const ri = startRow + i;
    if (ri >= next.length) return;
    let row = next[ri];
    line.forEach((cell, j) => {
      const col = columns[startCol + j];
      if (col) row = setCell(row, col, cell, objectives);
    });
    if (row !== next[ri]) {
      next[ri] = row;
      changed.push(row.id);
    }
  });
  return { rows: next, changed };
}

const csvCell = (v: unknown) => {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** CSV export of the ledger (planned / actual / measured columns), UTF-8 BOM for Excel. */
export function toCsv(rows: WorkbenchRow[], factors: string[], objectives: ObjectiveSpec[]): string {
  const head = ["id", "status", ...factors.map((f) => `planned:${f}`), ...factors.map((f) => `actual:${f}`),
    ...objectives.map((o) => o.metric), "note", "tags"];
  const body = rows.map((r) => [r.id, r.status, ...factors.map((f) => r.planned_params?.[f]), ...factors.map((f) => r.actual_params?.[f]),
    ...objectives.map((o) => r.measurements?.[o.metric]), r.note ?? "", (r.tags ?? []).join(";")].map(csvCell).join(","));
  return "﻿" + [head.join(","), ...body].join("\n");
}

/** Field-level comparison of two ledger rows for ``StructuredDiff``. */
export function rowChanges(a: WorkbenchRow, b: WorkbenchRow): Change[] {
  const out: Change[] = [];
  const cmp = (path: string, x: unknown, y: unknown) => {
    const ex = x === undefined || x === null || x === "";
    const ey = y === undefined || y === null || y === "";
    if (ex && ey) return;
    if (JSON.stringify(x) === JSON.stringify(y)) return;
    out.push({ path, kind: ex ? "added" : ey ? "removed" : "changed", old: ex ? null : x, new: ey ? null : y, major: path.startsWith("measurements") });
  };
  cmp("status", a.status, b.status);
  for (const group of ["planned_params", "actual_params", "measurements"] as const) {
    const keys = new Set([...Object.keys(a[group] ?? {}), ...Object.keys(b[group] ?? {})]);
    for (const k of keys) cmp(`${group}.${k}`, (a[group] as Record<string, unknown>)?.[k], (b[group] as Record<string, unknown>)?.[k]);
  }
  cmp("note", a.note, b.note);
  cmp("tags", (a.tags ?? []).join(", "), (b.tags ?? []).join(", "));
  return out;
}

export type LoopTone = "converged" | "failed" | "paused" | "running" | "idle";

/** Normalised loop state of a campaign for the status tag. */
export function loopTone(status: string | undefined, converged: boolean | undefined): LoopTone {
  if (status === "converged" || converged) return "converged";
  if (status === "failed" || status === "paused" || status === "running") return status;
  return "idle";
}

/** Rounds + converged flag from a campaign response (loop_status first, loop_history as fallback). */
export function loopSummary(loopStatus: { rounds?: number; converged?: boolean; status?: string; message?: string } | null | undefined,
  history: Record<string, unknown>[] | undefined) {
  const h = history ?? [];
  const last = h[h.length - 1] as { converged?: boolean } | undefined;
  return {
    rounds: loopStatus?.rounds ?? h.length,
    converged: Boolean(loopStatus?.converged ?? last?.converged),
    status: String(loopStatus?.status || "idle"),
    message: String(loopStatus?.message || ""),
  };
}

/** Strategy label of a campaign created from a DOE plan (as legacy ``workbenchStrategyForPlan``). */
export function strategyForPlan(plan: Pick<DoePlan, "design">, engineHint?: string | null): string {
  return plan.design === "ai_active" ? `BayBE-${engineHint ?? "loop"}-next` : `DOE-${plan.design}`;
}

/** Experiment records (POST /api/experiments) from completed ledger rows with measurements. */
export function trainingRecords(rows: WorkbenchRow[], metrics: string[], domain: string, projectId?: string | null) {
  const out: { domain: string; project_id?: string | null; factors: Record<string, number>; cure_temperature_c: number | null;
    measured: Record<string, number>; source: string }[] = [];
  for (const r of rows) {
    if (r.status !== "Completed") continue;
    const measured: Record<string, number> = {};
    for (const m of metrics) {
      const v = parseNumeric(r.measurements?.[m]);
      if (v != null) measured[m] = v;
    }
    if (!Object.keys(measured).length) continue;
    const actual = Object.fromEntries(Object.entries(r.actual_params ?? {}).filter(([, v]) => typeof v === "number")) as Record<string, number>;
    const factors = { ...(r.planned_params ?? {}), ...actual };
    out.push({ domain, project_id: projectId, factors, cure_temperature_c: factors.cure_temperature_c ?? null, measured, source: "workbench" });
  }
  return out;
}
