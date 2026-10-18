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
}

export const ROLES = ["resin", "hardener", "solvent", "pigment", "filler", "inhibitor", "additive", "surfactant", "acid", "base", "water", "other"];

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
