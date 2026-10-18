import type { DocContent, DocSection } from "./types";

export interface PairedField {
  key: string;
  labelZh: string;
  labelEn: string;
  valueZh: unknown;
  valueEn: unknown;
  /** 字段只在一侧出现（结构不对称，校对时要警惕）。 */
  missingSide: "zh" | "en" | null;
}

export interface PairedSection {
  key: string;
  titleZh: string;
  titleEn: string;
  noZh: number | null;
  noEn: number | null;
  /** 章节只在一侧出现。 */
  missingSide: "zh" | "en" | null;
  fields: PairedField[];
  /** 两侧表格列数/行数不一致时为 true（内容是否相等由人判断）。 */
  tablesMismatch: boolean;
  /** 两侧段落数不一致。 */
  paragraphsMismatch: boolean;
}

export interface ProofreadParity {
  sectionsZh: number;
  sectionsEn: number;
  fieldsZh: number;
  fieldsEn: number;
  missingSections: string[];
  missingFields: string[];
}

/**
 * 按章节 key / 字段 key 配对中英两份文档内容（3-16）。
 * DocumentPage 的中英对照是按数组索引配对的：两侧章节顺序/数量不同时
 * 会错位。校对页必须按 key 配对，缺失项显式标出。
 */
export function pairSections(zh: DocContent, en: DocContent): PairedSection[] {
  const enByKey = new Map(en.sections.map((s) => [s.key, s]));
  const seen = new Set<string>();
  const out: PairedSection[] = [];
  const pair = (a: DocSection | undefined, b: DocSection | undefined): PairedSection => {
    const fieldsEn = new Map((b?.fields ?? []).map((f) => [f.key, f]));
    const fields: PairedField[] = [];
    const seenF = new Set<string>();
    for (const f of a?.fields ?? []) {
      seenF.add(f.key);
      const g = fieldsEn.get(f.key);
      fields.push({ key: f.key, labelZh: f.label, labelEn: g?.label ?? "", valueZh: f.value,
                    valueEn: g?.value ?? null, missingSide: g ? null : "en" });
    }
    for (const g of b?.fields ?? []) {
      if (!seenF.has(g.key)) {
        fields.push({ key: g.key, labelZh: "", labelEn: g.label, valueZh: null,
                      valueEn: g.value, missingSide: "zh" });
      }
    }
    const tablesMismatch = (a?.tables.length ?? 0) !== (b?.tables.length ?? 0) ||
      (a?.tables ?? []).some((t, i) => t.columns.length !== (b?.tables[i]?.columns.length ?? -1) ||
        t.rows.length !== (b?.tables[i]?.rows.length ?? -1));
    return { key: (a ?? b)!.key, titleZh: a?.title ?? "", titleEn: b?.title ?? "",
             noZh: a?.no ?? null, noEn: b?.no ?? null,
             missingSide: a && b ? null : a ? "en" : "zh",
             fields, tablesMismatch,
             paragraphsMismatch: (a?.paragraphs.length ?? 0) !== (b?.paragraphs.length ?? 0) };
  };
  for (const s of zh.sections) {
    seen.add(s.key);
    out.push(pair(s, enByKey.get(s.key)));
  }
  for (const s of en.sections) {
    if (!seen.has(s.key)) out.push(pair(undefined, s));
  }
  return out;
}

export function proofreadParity(zh: DocContent, en: DocContent): ProofreadParity {
  const paired = pairSections(zh, en);
  const missingSections = paired.filter((p) => p.missingSide).map((p) => p.key);
  const missingFields = paired.flatMap((p) => p.fields.filter((f) => f.missingSide).map((f) => `${p.key}.${f.key}`));
  return { sectionsZh: zh.sections.length, sectionsEn: en.sections.length,
           fieldsZh: zh.sections.reduce((n, s) => n + s.fields.length, 0),
           fieldsEn: en.sections.reduce((n, s) => n + s.fields.length, 0),
           missingSections, missingFields };
}
