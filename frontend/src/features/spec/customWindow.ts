import type { CustomWindowParam } from "./types";

/** Fixed window keys: custom params must not reuse them (backend gives fixed keys priority). */
export const KNOWN_WINDOW_KEYS = ["cure_temp_c", "cure_time_min", "film_thickness_um", "application_method"];

export type CustomWindowRow = CustomWindowParam & { values_text?: string };

/** Editor rows carry a transient comma-separated text field for discrete values. */
export function toEditorRows(rows: CustomWindowParam[]): CustomWindowRow[] {
  return rows.map((r) => ({ ...r, values_text: (r.values ?? []).join(", ") }));
}

export function fromEditorRows(rows: CustomWindowRow[]): CustomWindowParam[] {
  return rows.map((r) => {
    const { values_text, ...rest } = r;
    if (rest.kind === "discrete") {
      const values = (values_text ?? "").split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
      const seen = new Set<string>();
      return { ...rest, values: values.filter((v) => !seen.has(v) && seen.add(v)), low: undefined, high: undefined };
    }
    const { values, ...range } = rest;
    return range;
  });
}

/** Save-time gate for custom window rows; returns an error message or null. */
export function validateCustomWindow(rows: CustomWindowParam[]): string | null {
  const seen = new Set<string>();
  for (const r of rows) {
    const key = (r.key ?? "").trim();
    if (!key) return "自定义工艺参数的英文键不能为空";
    if (!/^[a-z0-9_]+$/.test(key)) return `自定义工艺参数键格式非法：${key}（仅小写字母、数字、下划线）`;
    if (KNOWN_WINDOW_KEYS.includes(key)) return `自定义工艺参数键与固定项冲突：${key}`;
    if (seen.has(key)) return `自定义工艺参数键重复：${key}`;
    seen.add(key);
    if (!(r.label ?? "").trim()) return `自定义工艺参数 ${key}：中文名不能为空`;
    if (r.kind === "discrete") {
      if ((r.values ?? []).length < 2) return `自定义工艺参数 ${key}：离散列表至少需要 2 个取值`;
    } else {
      if (r.low == null || r.high == null) return `自定义工艺参数 ${key}：数值区间需要上下限`;
      if (r.low >= r.high) return `自定义工艺参数 ${key}：下限必须小于上限`;
    }
  }
  return null;
}
