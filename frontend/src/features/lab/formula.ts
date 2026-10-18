/** Pure formulation-editor logic (unit-tested): totals, normalisation, red-line checks. */
import i18n from "@/app/i18n";
// 纯逻辑模块不能用 useTranslation：用 i18n 单例（import 即初始化；未初始化时 i18n.t 返回 undefined，故必须 import）。
import type { SpecContract } from "@/features/spec/types";

export interface Row {
  uid: string;
  name: string;
  zh_name?: string;
  cas_no?: string;
  role?: string;
  weight_pct: number;
  locked?: boolean;
  hazards?: string; // display only, filled from the classification
  _class?: string;
  /** 成本 (元/kg 原料)，编辑器本机辅助字段；版本快照不持久化（见 annotations）。 */
  cost_per_kg?: number;
  /** VOC 含量 (g/L 原料)，编辑器本机辅助字段；同上。 */
  voc_g_l?: number;
  /** 预混料组名，编辑器本机辅助字段；同上。 */
  group?: string;
}

export const ROLES = ["resin", "hardener", "solvent", "pigment", "filler", "inhibitor", "additive", "surfactant", "acid", "base", "water", "other"];

/** Chinese labels for ROLES (the grid's help line and the detail panel). */
export const ROLE_LABELS: Record<string, string> = {
  resin: "树脂", hardener: "固化剂", solvent: "溶剂", pigment: "颜料", filler: "填料", inhibitor: "缓蚀剂",
  additive: "助剂", surfactant: "表面活性剂", acid: "酸", base: "碱", water: "水", other: "其他",
};

const ROLE_CAS: Record<string, string> = {
  "7732-18-5": "water", "1330-20-7": "solvent", "108-88-3": "solvent", "67-64-1": "solvent", "78-93-3": "solvent",
  "25068-38-6": "resin", "13463-67-7": "pigment", "7440-66-6": "pigment", "1310-73-2": "base", "7664-38-2": "acid",
};

const ROLE_KEYWORDS: { role: string; words: string[] }[] = [
  { role: "pigment", words: ["钛白", "tio2", "titanium dioxide", "锌粉", "zinc powder", "炭黑", "carbon black", "氧化铁", "iron oxide", "铝粉", "aluminium", "aluminum powder", "颜料", "pigment"] },
  { role: "filler", words: ["碳酸钙", "calcium carbonate", "滑石", "talc", "硅灰石", "wollastonite", "重晶石", "barite", "高岭土", "kaolin", "填料", "filler", "硅微粉"] },
  { role: "resin", words: ["环氧", "epoxy", "聚酯", "polyester", "聚氨酯", "polyurethane", "丙烯酸", "acrylic", "醇酸", "alkyd", "树脂", "resin", "氟碳", "fluorocarbon"] },
  { role: "hardener", words: ["固化剂", "hardener", "curing agent", "固化", "异氰酸酯", "isocyanate", "胺固化", "polyamide"] },
  { role: "inhibitor", words: ["缓蚀", "inhibitor", "铬酸", "chromate", "钼酸", "molybdate", "磷酸锌", "zinc phosphate", "防锈", "antirust"] },
  { role: "surfactant", words: ["表面活性", "surfactant", "分散剂", "dispersant", "润湿", "wetting"] },
  { role: "solvent", words: ["二甲苯", "xylene", "甲苯", "toluene", "丁酮", "mek", "丙酮", "acetone", "醋酸丁酯", "butyl acetate", "pma", "异丙醇", "isopropanol", "乙醇", "ethanol", "溶剂", "solvent", "丁醇", "butanol"] },
  { role: "acid", words: ["磷酸", "phosphoric", "酸", "acid"] },
  { role: "base", words: ["氢氧化钠", "sodium hydroxide", "naoh", "氢氧化钾", "碱", "alkali"] },
  { role: "water", words: ["去离子水", "deionized", "纯水", "水", "water"] },
  { role: "additive", words: ["流平", "leveling", "消泡", "defoamer", "增稠", "thickener", "催干", "drier", "防沉降", "助剂", "additive"] },
];

/**
 * Rule-based role suggestion from the ingredient's name / Chinese name / CAS.
 * Transparent heuristic (not an LLM call): the caller labels it as a suggestion
 * the user confirms. Returns "other" when nothing matches.
 */
export function suggestRole(row: Pick<Row, "name" | "zh_name" | "cas_no">): string {
  const cas = (row.cas_no || "").trim();
  if (cas && ROLE_CAS[cas]) return ROLE_CAS[cas];
  const hay = `${row.name || ""} ${row.zh_name || ""}`.toLowerCase();
  for (const { role, words } of ROLE_KEYWORDS) {
    if (words.some((w) => hay.includes(w))) return role;
  }
  return "other";
}

const r4 = (x: number) => Math.round(x * 1e4) / 1e4;

export function total(rows: Row[]): number {
  return r4(rows.reduce((s, r) => s + (Number(r.weight_pct) || 0), 0));
}

/** Scale the unlocked rows so the whole formulation sums to 100 %. */
export function normalize(rows: Row[]): Row[] {
  const locked = rows.filter((r) => r.locked).reduce((s, r) => s + (Number(r.weight_pct) || 0), 0);
  const free = rows.filter((r) => !r.locked).reduce((s, r) => s + (Number(r.weight_pct) || 0), 0);
  const room = 100 - locked;
  if (free <= 0 || room < 0) throw new Error(room < 0
    ? i18n.t("formula.locked_over", "锁定组分合计已超过 100%") : i18n.t("formula.no_normalizable", "没有可归一化的组分"));
  const k = room / free;
  const out = rows.map((r) => (r.locked ? r : { ...r, weight_pct: r4((Number(r.weight_pct) || 0) * k) }));
  return fixRounding(out);
}

/** Give one row (typically the solvent) whatever makes the total 100 %. */
export function fillWith(rows: Row[], uid: string): Row[] {
  const others = rows.filter((r) => r.uid !== uid).reduce((s, r) => s + (Number(r.weight_pct) || 0), 0);
  const balance = r4(100 - others);
  if (balance < 0) throw new Error(i18n.t("formula.remainder_over", "其余组分合计已超过 100%"));
  return rows.map((r) => (r.uid === uid ? { ...r, weight_pct: balance } : r));
}

function fixRounding(rows: Row[]): Row[] {
  const diff = r4(100 - total(rows));
  if (diff === 0) return rows;
  const target = [...rows].filter((r) => !r.locked).sort((a, b) => b.weight_pct - a.weight_pct)[0];
  return rows.map((r) => (r === target ? { ...r, weight_pct: r4(r.weight_pct + diff) } : r));
}

export interface Violation { uid?: string; message: string }

/** Client-side red-line check against the project's spec contract (the release gate re-checks server-side,
 * after raw materials are exploded into substances). */
export function redLineViolations(rows: Row[], spec: SpecContract | undefined, cls?: { pictograms?: { code: string }[]; signal_word?: string | null }): Violation[] {
  const out: Violation[] = [];
  if (!spec) return out;
  for (const rl of spec.red_lines ?? []) {
    const hit = rows.filter((r) => r.cas_no && r.cas_no.trim() === rl.cas);
    const pct = r4(hit.reduce((s, r) => s + (Number(r.weight_pct) || 0), 0));
    if (hit.length && pct > (rl.max_pct ?? 0)) {
      hit.forEach((r) => out.push({
      uid: r.uid,
      message: rl.reason
        ? i18n.t("formula.redline_over_reason", "{{name}}：{{pct}}% 超过红线 {{max}}%（{{reason}}）",
          { name: rl.name || rl.cas, pct, max: rl.max_pct ?? 0, reason: rl.reason })
        : i18n.t("formula.redline_over", "{{name}}：{{pct}}% 超过红线 {{max}}%",
          { name: rl.name || rl.cas, pct, max: rl.max_pct ?? 0 }),
    }));
    }
  }
  const pictos = (cls?.pictograms ?? []).map((p) => p.code);
  const bad = pictos.filter((p) => (spec.forbidden_pictograms ?? []).includes(p));
  if (bad.length) out.push({ message: i18n.t("formula.forbidden_pictogram", "出现禁止的象形图 {{list}}", { list: bad.join("、") }) });
  const sw = cls?.signal_word;
  if (sw && spec.forbidden_signal_word && (spec.forbidden_signal_word === "警告" || sw === spec.forbidden_signal_word)) {
    out.push({ message: i18n.t("formula.signal_word_violation", "信号词为“{{sw}}”，违反规格契约", { sw }) });
  }
  return out;
}

export function newRow(): Row {
  return { uid: Math.random().toString(36).slice(2), name: "", weight_pct: 0 };
}

/** Cost contribution of one row for 1 kg of formulation (元): mass fraction × 原料单价. */
export function rowCost(r: Row): number {
  return r4((Number(r.weight_pct) || 0) / 100 * (r.cost_per_kg ?? 0));
}

/** Total formulation cost (元/kg 配方). */
export function totalCost(rows: Row[]): number {
  return r4(rows.reduce((s, r) => s + rowCost(r), 0));
}

/**
 * VOC contribution of one row (g/L 配方): mass-weighted estimate — the exact
 * mix needs each ingredient's density, which the editor does not carry, so
 * the panel labels the total as an estimate on a same-density basis.
 */
export function rowVoc(r: Row): number {
  return r4((Number(r.weight_pct) || 0) / 100 * (r.voc_g_l ?? 0));
}

/** Total formulation VOC estimate (g/L). */
export function totalVoc(rows: Row[]): number {
  return r4(rows.reduce((s, r) => s + rowVoc(r), 0));
}

export interface GroupSubtotal { group: string; weight_pct: number; cost: number; voc: number; count: number }

/** Per-premix-group subtotals; rows without a group land in the "" (ungrouped) bucket. */
export function groupSubtotals(rows: Row[]): GroupSubtotal[] {
  const by = new Map<string, GroupSubtotal>();
  for (const r of rows) {
    const g = (r.group || "").trim();
    const cur = by.get(g) ?? { group: g, weight_pct: 0, cost: 0, voc: 0, count: 0 };
    cur.weight_pct = r4(cur.weight_pct + (Number(r.weight_pct) || 0));
    cur.cost = r4(cur.cost + rowCost(r));
    cur.voc = r4(cur.voc + rowVoc(r));
    cur.count += 1;
    by.set(g, cur);
  }
  return [...by.values()].sort((a, b) => b.weight_pct - a.weight_pct);
}

/**
 * Editor-only annotations (cost / VOC / premix group). The backend version
 * snapshot is a pydantic-validated ``Formulation`` — unknown ingredient
 * fields are dropped on save — so these survive only in the browser's
 * localStorage, keyed by ingredient identity (name + CAS) within a lineage.
 * The editor still sends them in the save payload for forward compatibility.
 */
export type RowAnnotation = { cost_per_kg?: number; voc_g_l?: number; group?: string };

export const annotationKey = (r: Pick<Row, "name" | "cas_no">) => `${(r.name || "").trim()}||${(r.cas_no || "").trim()}`;

export function extractAnnotations(rows: Row[]): Record<string, RowAnnotation> {
  const out: Record<string, RowAnnotation> = {};
  for (const r of rows) {
    const ann: RowAnnotation = {};
    if (r.cost_per_kg != null) ann.cost_per_kg = r.cost_per_kg;
    if (r.voc_g_l != null) ann.voc_g_l = r.voc_g_l;
    if (r.group) ann.group = r.group;
    if (Object.keys(ann).length) out[annotationKey(r)] = ann;
  }
  return out;
}

export function applyAnnotations(rows: Row[], anns: Record<string, RowAnnotation>): Row[] {
  return rows.map((r) => {
    const a = anns[annotationKey(r)];
    return a ? { ...r, ...a } : r;
  });
}
