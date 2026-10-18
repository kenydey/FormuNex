/** Pure helpers of the knowledge library pages (unit-tested in logic.test.ts). */
import type { EmbodimentDraft, Evidence, ManifestItem, ProbeHit, SourceStatus, TablePropertyView } from "./types";

/** POST /api/sources/export takes at most this many documents per file. */
export const KB_EXPORT_MAX = 10;
export const KB_EXPORT_FORMATS = ["docx", "pdf", "html", "md"] as const;
export type KbExportFormat = (typeof KB_EXPORT_FORMATS)[number];

export const UPLOAD_ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";
/** POST /api/ingest/batch rejects more than this many files per request. */
export const UPLOAD_BATCH_MAX = 20;

/** Split a comma / semicolon separated list, trimming blanks (also accepts the full-width comma). */
export function splitList(text: string, sep: "," | ";" = ","): string[] {
  const re = sep === "," ? /[,，]/ : /[;；]/;
  return text.split(re).map((s) => s.trim()).filter(Boolean);
}

/** Chunk an array into groups of at most *size*. */
export function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

export type StatusColor = "success" | "warning" | "error" | "default";

/** Backend availability of a search source → badge status (green / yellow / red / gray). */
export function sourceStatusColor(st?: SourceStatus): StatusColor {
  if (!st) return "default";
  if (!st.available) return "error";
  if (st.offline_fallback) return "warning";
  return "success";
}

/** Types sent to /api/search/stream (``local`` is ingest-only). */
export function searchableTypes<T extends string>(selected: T[]): T[] {
  return selected.filter((t) => t !== "local");
}

/** Whether "ingest fulltext" can be offered for a search hit (patents always; literature when OA / DOI / arXiv). */
export function canIngestFulltext(e: Evidence): boolean {
  if (!e.identifier || e.is_seed_corpus) return false;
  const s = (e.source || "").toLowerCase();
  if (s.includes("surechembl") || s.includes("patent") || s === "uspto" || s === "epo") return true;
  if (["literature", "openalex", "arxiv", "semantic", "crossref"].some((k) => s.includes(k))) {
    return !!(e.is_oa || e.oa_pdf_url || /10\.\d{4,9}\//.test(e.identifier) || /arxiv/i.test(e.identifier));
  }
  return false;
}

/** Merge new evidence into a list, de-duplicated by identifier (or title). */
export function mergeEvidence(prev: Evidence[], next: Evidence[]): Evidence[] {
  const seen = new Set(prev.map((e) => e.identifier || e.title));
  const out = [...prev];
  for (const e of next) {
    const k = e.identifier || e.title;
    if (!k || seen.has(k)) continue;
    seen.add(k);
    out.push(e);
  }
  return out;
}

/** Evidence and progress counters carried by one /api/search/stream task event. */
export function parseSearchStreamData(data: Record<string, unknown> | null | undefined): {
  evidence: Evidence[]; total: number; sourcesDone: string[]; sourcesPending: string[]; usedSeedFallback: boolean;
} {
  if (!data) return { evidence: [], total: 0, sourcesDone: [], sourcesPending: [], usedSeedFallback: false };
  const evidence = Array.isArray(data.evidence) ? (data.evidence as Evidence[]) : [];
  return {
    evidence,
    total: typeof data.total === "number" ? data.total : evidence.length,
    sourcesDone: Array.isArray(data.sources_done) ? (data.sources_done as string[]) : [],
    sourcesPending: Array.isArray(data.sources_pending) ? (data.sources_pending as string[]) : [],
    usedSeedFallback: data.used_seed_fallback === true || evidence.some((e) => e.is_seed_corpus),
  };
}

export const TABLE_KINDS = ["recipe", "performance", "tds_sds", "other"] as const;
export const TABLE_KIND_COLOR: Record<string, string> = { recipe: "green", performance: "blue", tds_sds: "gold", other: "default" };

export function tableKind(kind?: string): (typeof TABLE_KINDS)[number] {
  return (TABLE_KINDS as readonly string[]).includes(kind ?? "") ? (kind as (typeof TABLE_KINDS)[number]) : "other";
}

/** One normalised table property as text, e.g. ``粘度: 1200 mPa·s``. */
export function propertyText(p: TablePropertyView): string {
  const name = p.name_normalized || p.name || "—";
  const value = p.value !== null && p.value !== undefined && p.value !== "" ? String(p.value) : (p.raw_text ?? "");
  const unit = p.unit_normalized || p.unit || "";
  return `${name}: ${value}${unit ? ` ${unit}` : ""}`.trim();
}

export function cellText(v: unknown): string {
  return v === null || v === undefined ? "" : String(v);
}

// ── literature manifest ────────────────────────────────────────────────────────────────

export function hasFulltext(it: ManifestItem): boolean {
  return !!it.has_fulltext || it.enrich_status === "fetched";
}

/** Citation locator, e.g. ``p.3 / Fig.2 / Tab.1``; empty when none. */
export function formatLocator(it: ManifestItem): string {
  const loc = it.locator;
  if (!loc) return "";
  const parts: string[] = [];
  if (loc.page != null) parts.push(`p.${loc.page}`);
  if (loc.figure) parts.push(`Fig.${loc.figure}`);
  if (loc.table) parts.push(`Tab.${loc.table}`);
  return parts.join(" / ");
}

export interface CoverageCounts { total: number; doi: number; snippet: number; fulltext: number; match: number; noMatch: number; pending: number }

/** Seven coverage counters of a manifest (or of its frozen subset). */
export function manifestCoverage(items: ManifestItem[]): CoverageCounts {
  const total = items.length;
  const match = items.filter((i) => i.screening === "match").length;
  const noMatch = items.filter((i) => i.screening === "no_match").length;
  return {
    total,
    doi: items.filter((i) => (i.doi || "").trim()).length,
    snippet: items.filter((i) => (i.snippet || "").trim()).length,
    fulltext: items.filter(hasFulltext).length,
    match,
    noMatch,
    pending: total - match - noMatch,
  };
}

/** The items a manifest view is about: the frozen subset once frozen, else every candidate. */
export function scopedManifestItems(items: ManifestItem[], frozen: { item_ids?: string[] } | null | undefined): ManifestItem[] {
  if (!frozen) return items;
  const ids = new Set(frozen.item_ids ?? []);
  return items.filter((it) => ids.has(it.id));
}

export type ManifestStyle = "list" | "citation" | "table";

function mdCell(text: string): string {
  return text.replace(/\|/g, "｜").replace(/\n/g, " ").slice(0, 120);
}

/** Markdown snapshot of the manifest in one of three styles (saved to the project shelf). */
export function buildManifestMarkdown(style: ManifestStyle, heading: string, items: ManifestItem[]): string {
  const lines = [`# ${heading}`, ""];
  if (style === "citation") {
    items.forEach((it, i) => {
      const loc = formatLocator(it);
      lines.push(`[${i + 1}] ${it.title || it.id}${it.doi ? `. DOI: ${it.doi}` : ""}${loc ? ` (${loc})` : ""}`);
    });
  } else if (style === "table") {
    lines.push("| # | 标题 | DOI | 来源 | 筛选 | 全文 |", "|---|---|---|---|---|---|");
    items.forEach((it, i) => {
      lines.push(`| ${i + 1} | ${mdCell(it.title || it.id)} | ${mdCell(it.doi || "—")} | ${mdCell(it.source || it.evidence_class || "—")} | `
        + `${mdCell(it.screening || "unset")} | ${hasFulltext(it) ? "✓" : "—"} |`);
    });
  } else {
    items.forEach((it, i) => {
      lines.push(`## ${i + 1}. ${it.title || it.id}`);
      if (it.doi) lines.push(`DOI: ${it.doi}`);
      const loc = formatLocator(it);
      if (loc) lines.push(`定位: ${loc}`);
      lines.push(`筛选: ${it.screening || "unset"} · 来源: ${it.source || it.evidence_class || "—"} · 全文: ${hasFulltext(it) ? "有" : "无"}`);
      if ((it.snippet || "").trim()) lines.push(`> ${it.snippet}`);
      lines.push("");
    });
  }
  return lines.join("\n");
}

/** File name on the project shelf: sanitised prefix + timestamp. */
export function shelfFilename(prefix: string, ext: string, now = new Date()): string {
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const safe = prefix.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "export";
  return `${safe}_${stamp}.${ext.replace(/^\./, "")}`;
}

/** Locator form → request body; ``null`` when the page is not a positive integer. */
export function locatorBody(form: { page?: string | number | null; figure?: string; table?: string }):
  { page: number | null; figure: string | null; table: string | null } | null {
  const raw = form.page === undefined || form.page === null ? "" : String(form.page).trim();
  const page = raw === "" ? null : Number(raw);
  if (page !== null && (!Number.isInteger(page) || page < 1)) return null;
  return { page, figure: form.figure?.trim() || null, table: form.table?.trim() || null };
}

export const SCREENING_COLOR: Record<string, string> = { match: "success", no_match: "error", uncertain: "warning", unset: "default" };

// ── retrieval probe ────────────────────────────────────────────────────────────────────

export function fmtScore(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : v.toFixed(3);
}

/** Rank movement caused by the reranker: ``↑2`` / ``↓1`` / ``·``; ``—`` without rerank. */
export function rankDelta(hit: ProbeHit): string {
  if (hit.rank_before_rerank == null || hit.rerank_score == null) return "—";
  const d = hit.rank_before_rerank - hit.rank;
  if (d > 0) return `↑${d}`;
  if (d < 0) return `↓${Math.abs(d)}`;
  return "·";
}

export type ProbeScope = "project" | "project_global" | "global";

export function probeScopeParams(projectId: string | null, scope: ProbeScope): { project_id: string | null; include_global: boolean } {
  if (!projectId || scope === "global") return { project_id: null, include_global: true };
  return { project_id: projectId, include_global: scope !== "project" };
}

export interface TrendPoint { at: string; mrr: number; recall_at_k: number; passed: number; total: number }

const TREND_KEY = "formunex.golden_mrr_trend";
const TREND_MAX = 12;

/** Golden-run trend kept in this browser only (a per-viewer convenience, like the legacy panel). */
export function loadTrend(storage: Pick<Storage, "getItem"> | null = safeStorage()): TrendPoint[] {
  try {
    const raw = storage?.getItem(TREND_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as TrendPoint[]).slice(-TREND_MAX) : [];
  } catch {
    return [];
  }
}

export function pushTrend(point: TrendPoint, storage: Pick<Storage, "getItem" | "setItem"> | null = safeStorage()): TrendPoint[] {
  const next = [...loadTrend(storage), point].slice(-TREND_MAX);
  try {
    storage?.setItem(TREND_KEY, JSON.stringify(next));
  } catch {
    // quota / blocked storage: the trend just is not remembered
  }
  return next;
}

/** True when the last MRR dropped by more than 0.02 against the run before. */
export function trendDropped(trend: TrendPoint[]): boolean {
  if (trend.length < 2) return false;
  return trend[trend.length - 1].mrr + 0.02 < trend[trend.length - 2].mrr;
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function fmtEpoch(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, { hour12: false });
}

export function qualityTone(score: number | null | undefined): "success" | "warning" | "danger" | undefined {
  if (score == null) return undefined;
  return score >= 70 ? "success" : score >= 45 ? "warning" : "danger";
}

export const PARSE_PROFILES = ["low", "mid", "cloud", "high"] as const;

/** Requirements of a parse profile the host does not meet (the profile then degrades). */
export function missingForProfile(profile: string, avail: { gpu_available: boolean; mineru_key_present: boolean } | null): ("gpu" | "mineru")[] {
  const needs: Record<string, ("gpu" | "mineru")[]> = { low: [], mid: ["mineru"], cloud: ["mineru"], high: ["gpu"] };
  return (needs[profile] ?? []).filter((n) => (n === "gpu" ? !avail?.gpu_available : !avail?.mineru_key_present));
}

// ── embodiment drafts ──────────────────────────────────────────────────────────────────


/** Fulltext drafts confirm via /api/formulations, SureChEMBL-only drafts via /api/surechembl. */
export function draftConfirmPath(origin: string): string {
  return origin === "surechembl" ? "/api/surechembl/confirm-example-draft" : "/api/formulations/confirm-embodiment-draft";
}

/** The draft to confirm when the user picked embodiment *idx*: its ingredients become the formulation. */
export function draftForEmbodiment(draft: EmbodimentDraft, idx: number): EmbodimentDraft {
  const emb = draft.embodiments?.[idx];
  if (!emb) return draft;
  const ingredients = emb.ingredients ?? [];
  return {
    ...draft,
    amount_source: emb.amount_source || draft.amount_source,
    ingredients_detail: ingredients,
    formulation: {
      ...draft.formulation,
      ingredients: ingredients.map((ing) => ({ name: ing.name, role: ing.role || "additive", weight_pct: ing.weight_pct })),
    },
  };
}
