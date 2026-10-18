/** STORM 报告标题抽取 + slug（锚点跳页用，见 StormReportTab）。纯函数，可单测。 */

export interface StormTocEntry {
  level: number;
  title: string;
  slug: string;
}

/** GitHub 风格 slug：保留 CJK，空格转连字符，去标点与 Markdown 格式标记。 */
export function stormSlug(title: string): string {
  const base = title
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s+/g, "-");
  return base || "section";
}

/** 从 Markdown 抽取标题（h1–h6），跳过围栏代码块；重复标题按 GitHub 规则加 -1/-2 后缀。 */
export function extractStormToc(markdown: string): StormTocEntry[] {
  const entries: StormTocEntry[] = [];
  const used = new Map<string, number>();
  let inFence = false;
  for (const line of (markdown || "").split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;
    const title = m[2].replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[*_`~]/g, "").trim();
    if (!title) continue;
    const base = stormSlug(title);
    const n = used.get(base) ?? 0;
    used.set(base, n + 1);
    entries.push({ level: m[1].length, title, slug: n ? `${base}-${n}` : base });
  }
  return entries;
}
