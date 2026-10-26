/**
 * Requirement-level process constraints (legacy ConstraintsEditor / constants/constraints.ts).
 *
 * Catalogue constraints live as top-level Requirement fields (voc_limit_gpl, cure_temperature_c, …);
 * free-form ones live in ``requirement.constraint_values`` ({name: value}). Which catalogue entries are
 * shown is persisted per project in ``workspace.active_constraints``.
 */

export type ConstraintKey =
  | "voc_limit_gpl"
  | "cure_temperature_c"
  | "ph_target"
  | "salt_spray_hours"
  | "film_weight_gsm"
  | "cleaning_efficiency";

export interface ConstraintDef {
  key: ConstraintKey;
  /** Chinese catalogue label — also the name under which the backend may mirror it in constraint_values. */
  zh: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  domains: string[] | "all";
}

export const CONSTRAINT_CATALOG: ConstraintDef[] = [
  { key: "voc_limit_gpl", zh: "VOC 上限", unit: "g/L", min: 0, max: 700, step: 10, domains: "all" },
  { key: "cure_temperature_c", zh: "固化温度上限", unit: "°C", min: 20, max: 300, step: 5, domains: ["anticorrosion_coating"] },
  { key: "ph_target", zh: "pH 目标", unit: "", min: 0, max: 14, step: 0.5, domains: ["degreaser"] },
  { key: "salt_spray_hours", zh: "耐盐雾目标", unit: "h", min: 0, max: 3000, step: 50, domains: ["anticorrosion_coating", "surface_treatment"] },
  { key: "film_weight_gsm", zh: "干膜重目标", unit: "g/m²", min: 0, max: 200, step: 5, domains: ["anticorrosion_coating"] },
  { key: "cleaning_efficiency", zh: "清洗率目标", unit: "%", min: 0, max: 100, step: 1, domains: ["degreaser"] },
];

/** Fields that the backend treats as "no limit" when null (the others default to 0). */
const NULLABLE: ConstraintKey[] = ["voc_limit_gpl", "cure_temperature_c", "ph_target"];

export type ConstraintRequirement = {
  domain: string;
  constraint_values?: Record<string, number> | null;
  [k: string]: unknown;
};

export const constraintDef = (key: ConstraintKey) => CONSTRAINT_CATALOG.find((c) => c.key === key);

export function appliesToDomain(def: ConstraintDef, domain: string): boolean {
  return def.domains === "all" || def.domains.includes(domain);
}

export function defaultConstraintsForDomain(domain: string): ConstraintKey[] {
  if (domain === "anticorrosion_coating") return ["voc_limit_gpl", "cure_temperature_c"];
  if (domain === "degreaser") return ["voc_limit_gpl", "ph_target"];
  if (domain === "autodeposition_coating") return ["voc_limit_gpl", "salt_spray_hours"];
  return ["voc_limit_gpl"];
}

/** Current value of a catalogue constraint, or a sensible starting value when unset. */
export function getConstraintValue(req: ConstraintRequirement, key: ConstraintKey): number {
  const v = req[key];
  if (typeof v === "number" && !Number.isNaN(v)) return v;
  if (key === "ph_target") return 12;
  const def = constraintDef(key);
  return def ? (def.min + def.max) / 2 : 0;
}

export function setConstraintValue<R extends ConstraintRequirement>(req: R, key: ConstraintKey, value: number): R {
  return { ...req, [key]: value };
}

export function clearConstraintValue<R extends ConstraintRequirement>(req: R, key: ConstraintKey): R {
  return { ...req, [key]: NULLABLE.includes(key) ? null : 0 };
}

/** Free-form constraint_values entries that are not mirrors of a catalogue constraint. */
export function customConstraints(req: ConstraintRequirement): [string, number][] {
  const labels = new Set(CONSTRAINT_CATALOG.map((c) => c.zh));
  return Object.entries(req.constraint_values ?? {}).filter(([name]) => !labels.has(name));
}

export type CustomConstraintKind = "upper" | "lower" | "range" | "target";

export interface CustomConstraintRow {
  name: string;
  unit?: string;
  kind: CustomConstraintKind;
  low?: number | null;
  high?: number | null;
  value?: number | null;
}

export type CustomConstraintRequirement = ConstraintRequirement & { custom_constraints?: CustomConstraintRow[] | null };

/** Rows for the editor: stored structured rows, or legacy single-value entries migrated in place (kind=target). */
export function customConstraintRows(req: CustomConstraintRequirement): CustomConstraintRow[] {
  const stored = (req.custom_constraints ?? []).filter((r) => r && r.name.trim());
  if (stored.length > 0) return stored;
  return customConstraints(req).map(([name, value]) => ({ name, unit: "", kind: "target" as const, value }));
}

/** Validate structured rows; returns an error message or null. */
export function validateCustomConstraintRows(rows: CustomConstraintRow[]): string | null {
  const seen = new Set<string>();
  for (const r of rows) {
    const name = (r.name ?? "").trim();
    if (!name) return "自定义约束名称不能为空";
    if (seen.has(name)) return `自定义约束名称重复：${name}`;
    seen.add(name);
    if (r.kind === "range") {
      if (r.low == null || r.high == null) return `自定义约束 ${name}：区间需要上下限`;
      if (r.low >= r.high) return `自定义约束 ${name}：下限必须小于上限`;
    } else if (r.value == null) {
      return `自定义约束 ${name}：需要一个值`;
    }
  }
  return null;
}

/**
 * Write rows back: store the structured rows and keep the constraint_values
 * single-value mirror in sync (upper/lower/target rows mirror their value;
 * range rows write no mirror). Mirrors of rows that disappeared are removed;
 * unrelated constraint_values entries are left untouched.
 */
export function syncCustomConstraints<R extends CustomConstraintRequirement>(
  req: R, rowsBefore: CustomConstraintRow[], rowsAfter: CustomConstraintRow[],
): R {
  const single = new Map(rowsAfter.filter((r) => r.kind !== "range").map((r) => [r.name.trim(), r.value ?? 0]));
  const cv = { ...(req.constraint_values ?? {}) };
  for (const r of rowsBefore) {
    const name = r.name.trim();
    if (name && !single.has(name)) delete cv[name];
  }
  for (const [name, value] of single) cv[name] = value;
  return { ...req, custom_constraints: rowsAfter, constraint_values: cv };
}

export function setCustomConstraint<R extends ConstraintRequirement>(req: R, name: string, value: number): R {
  const n = name.trim();
  if (!n) return req;
  return { ...req, constraint_values: { ...(req.constraint_values ?? {}), [n]: value } };
}

export function removeCustomConstraint<R extends ConstraintRequirement>(req: R, name: string): R {
  const cv = { ...(req.constraint_values ?? {}) };
  delete cv[name];
  return { ...req, constraint_values: cv };
}

/** Add a catalogue constraint: activate it and materialise its (default) value. */
export function addConstraint<R extends ConstraintRequirement>(req: R, keys: ConstraintKey[], key: ConstraintKey): { req: R; keys: ConstraintKey[] } {
  if (keys.includes(key)) return { req, keys };
  return { req: setConstraintValue(req, key, getConstraintValue(req, key)), keys: [...keys, key] };
}

export function removeConstraint<R extends ConstraintRequirement>(req: R, keys: ConstraintKey[], key: ConstraintKey): { req: R; keys: ConstraintKey[] } {
  return { req: clearConstraintValue(req, key), keys: keys.filter((k) => k !== key) };
}

/** Reset to the domain defaults; constraints that do not apply to the domain are cleared. */
export function resetConstraints<R extends ConstraintRequirement>(req: R): { req: R; keys: ConstraintKey[] } {
  const keys = defaultConstraintsForDomain(req.domain);
  let next = req;
  for (const def of CONSTRAINT_CATALOG) if (!appliesToDomain(def, req.domain)) next = clearConstraintValue(next, def.key);
  for (const k of keys) next = setConstraintValue(next, k, getConstraintValue(next, k));
  return { req: next, keys };
}

/** Sanitise the persisted active list (unknown keys dropped). */
export function parseActiveConstraints(raw: unknown): ConstraintKey[] {
  if (!Array.isArray(raw)) return [];
  const known = new Set<string>(CONSTRAINT_CATALOG.map((c) => c.key));
  return raw.filter((k): k is ConstraintKey => typeof k === "string" && known.has(k));
}
