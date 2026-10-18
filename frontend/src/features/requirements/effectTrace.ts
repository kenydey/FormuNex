/**
 * Client-side preview of Requirement → recommend / DOE wiring (legacy utils/requirementEffectTrace.ts, mirrors the
 * backend's ``effect_trace``): which brief fields actually reach scoring / DOE and which are display-only.
 */

export type TraceStatus = "wired" | "unwired" | "display_only";

export interface TraceItem {
  field: string;
  kind: string;
  /** Raw data label (metric / lever name, ``name=value``); empty for the domain-default placeholders. */
  label: string;
  status: TraceStatus | string;
  consumers?: string[];
  detail?: string;
  /** Numeric value for catalogue constraints (rendered with a localised label). */
  value?: number;
}

interface TraceRequirement {
  objectives?: { metric?: string }[] | null;
  levers?: { name?: string }[] | null;
  voc_limit_gpl?: number | null;
  cure_temperature_c?: number | null;
  ph_target?: number | null;
  notes?: string | null;
  constraint_values?: Record<string, number | null> | null;
  [k: string]: unknown;
}

export function previewEffectTrace(req: TraceRequirement | null | undefined): TraceItem[] {
  if (!req) return [];
  const items: TraceItem[] = [];
  const objectives = (req.objectives ?? []).filter((o) => o?.metric);
  if (objectives.length) {
    for (const o of objectives) {
      items.push({ field: `objectives.${o.metric}`, kind: "objective", label: o.metric!, status: "wired", consumers: ["recommend", "doe"] });
    }
  } else {
    items.push({ field: "objectives", kind: "objective", label: "", status: "wired", consumers: ["recommend"] });
  }
  const levers = (req.levers ?? []).filter((l) => l?.name);
  if (levers.length) {
    for (const l of levers) items.push({ field: `levers.${l.name}`, kind: "lever", label: l.name!, status: "wired", consumers: ["doe"] });
  } else {
    items.push({ field: "levers", kind: "lever", label: "", status: "wired", consumers: ["doe"], detail: "resolve_levers" });
  }
  if (req.voc_limit_gpl != null) {
    items.push({ field: "voc_limit_gpl", kind: "constraint", label: `voc_limit_gpl=${req.voc_limit_gpl}`, value: req.voc_limit_gpl, status: "wired", consumers: ["validate", "recommend"] });
  }
  if (req.cure_temperature_c != null) {
    items.push({ field: "cure_temperature_c", kind: "constraint", label: `cure_temperature_c=${req.cure_temperature_c}`, value: req.cure_temperature_c, status: "wired", consumers: ["process"] });
  }
  if (req.ph_target != null) {
    items.push({ field: "ph_target", kind: "constraint", label: `ph_target=${req.ph_target}`, value: req.ph_target, status: "display_only" });
  }
  if (typeof req.notes === "string" && req.notes.trim()) {
    items.push({ field: "notes", kind: "meta", label: "notes", status: "display_only", detail: "llm_prompt" });
  }
  for (const [k, v] of Object.entries(req.constraint_values ?? {})) {
    if (v == null) continue;
    items.push({ field: `constraint_values.${k}`, kind: "constraint", label: `${k}=${v}`, status: "wired", consumers: ["recommend", "doe"] });
  }
  return items;
}

export function traceCounts(items: TraceItem[]): Record<"wired" | "unwired" | "display_only", number> {
  const out = { wired: 0, unwired: 0, display_only: 0 };
  for (const i of items) {
    if (i.status === "wired") out.wired++;
    else if (i.status === "unwired") out.unwired++;
    else out.display_only++;
  }
  return out;
}
