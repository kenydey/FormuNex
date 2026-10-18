/** Pure helpers of the research area (unit-tested in lib.test.ts). */
import { idsMatch } from "@/features/requirements/sourceRows";
import type {
  ChatTurn, EmbodimentDraft, Evidence, KbIngestDoc, KbIngestProgress, PreflightFinding, SearchProgress, SourceStatus, SourceType,
} from "./types";

/** Federated search connectors (legacy SourceTypePicker; ``local`` is the upload path, not a search source). */
export const SOURCE_TYPES: { id: SourceType; zh: string }[] = [
  { id: "patents", zh: "专利" },
  { id: "surechembl", zh: "专利化学 SureChEMBL" },
  { id: "literature", zh: "文献" },
  { id: "internet", zh: "互联网" },
  { id: "notebooklm", zh: "NotebookLM" },
];

export const DEFAULT_SOURCE_TYPES: SourceType[] = ["patents", "literature"];

/** Stable identity of an evidence row (legacy: ``identifier || title``). */
export const evidenceKey = (e: Pick<Evidence, "identifier" | "title">) => e.identifier || e.title;

/** Append *fresh* rows that are not already present (dedupe by key); returns the merged list and the new keys. */
export function mergeEvidence(existing: Evidence[], fresh: Evidence[]): { merged: Evidence[]; added: string[] } {
  const seen = new Set(existing.map(evidenceKey));
  const merged = [...existing];
  const added: string[] = [];
  for (const e of fresh) {
    const k = evidenceKey(e);
    if (!k || seen.has(k)) continue;
    seen.add(k);
    merged.push(e);
    added.push(k);
  }
  return { merged, added };
}

/** Body the chat endpoint validates: identifier/title/snippet non-empty, relevance clamped to [0, 1]. */
export function sanitizeEvidence(ev: Evidence): Evidence {
  const rel = Number(ev.relevance);
  const identifier = (ev.identifier || ev.title || "source").trim() || "source";
  const title = (ev.title || identifier).trim() || identifier;
  const snippet = (ev.snippet ?? "").trim();
  return { ...ev, source: (ev.source || "local").trim() || "local", identifier, title, snippet: snippet || title,
    relevance: Number.isFinite(rel) ? Math.min(1, Math.max(0, rel)) : 0.5 };
}

/** Parse the ``data`` of a federated-search progress event. */
export function parseSearchData(data: Record<string, unknown> | null | undefined): {
  evidence: Evidence[]; progress: Partial<SearchProgress>; usedSeedFallback: boolean;
  filterReport: import("./types").FilterReport | null; kbIngestTaskId: string | null;
  sourceStatus: Record<string, SourceStatus> | null;
} {
  if (!data) return { evidence: [], progress: {}, usedSeedFallback: false, filterReport: null, kbIngestTaskId: null, sourceStatus: null };
  const evidence = Array.isArray(data.evidence) ? (data.evidence as Evidence[]) : [];
  const rawReport = data.filter_report;
  return {
    evidence,
    usedSeedFallback: data.used_seed_fallback === true || evidence.some((e) => e.is_seed_corpus),
    filterReport: rawReport && typeof rawReport === "object" && !Array.isArray(rawReport) ? (rawReport as import("./types").FilterReport) : null,
    kbIngestTaskId: typeof data.kb_ingest_task_id === "string" ? data.kb_ingest_task_id : null,
    sourceStatus: data.source_status && typeof data.source_status === "object" ? (data.source_status as Record<string, SourceStatus>) : null,
    progress: {
      total: typeof data.total === "number" ? data.total : evidence.length,
      source: typeof data.source === "string" ? data.source : null,
      newCount: typeof data.new_count === "number" ? data.new_count : 0,
      sourcesDone: Array.isArray(data.sources_done) ? (data.sources_done as string[]) : [],
      sourcesPending: Array.isArray(data.sources_pending) ? (data.sources_pending as string[]) : [],
    },
  };
}

/** Per-document progress of the background KB build that a search kicks off. */
export function parseKbIngest(data: Record<string, unknown> | null | undefined): KbIngestProgress | null {
  if (!data || !Array.isArray(data.docs)) return null;
  const docs = data.docs as KbIngestProgress["docs"];
  const num = (v: unknown, d: number) => (typeof v === "number" ? v : d);
  return {
    docs,
    done: num(data.done, 0),
    total: num(data.total, docs.length),
    indexed: num(data.indexed, docs.filter((d) => d.status === "indexed").length),
    failed: num(data.failed, docs.filter((d) => d.status === "failed").length),
  };
}

/** Availability of a connector → antd Badge status. */
export function sourceBadge(st?: SourceStatus): "success" | "warning" | "error" | "default" {
  if (!st) return "default";
  if (!st.available) return "error";
  if (st.offline_fallback) return "warning";
  return "success";
}

/** CRAG stages that ``research_graph._emit`` sends for deep research, in order. */
export const CRAG_STAGES = [
  { id: "retrieve", zh: "检索" },
  { id: "grade", zh: "评估" },
  { id: "fallback", zh: "补搜" },
  { id: "generate", zh: "生成" },
  { id: "claim_check", zh: "核验" },
  { id: "regenerate", zh: "修正" },
] as const;

export function stageIndex(stage: string | null | undefined): number {
  const i = CRAG_STAGES.findIndex((s) => s.id === stage);
  return i >= 0 ? i : 0;
}

/** Overall percent: the task's own progress (0–1 or 0–100) when it reports one, else the stage position. */
export function deepPercent(stage: string | null | undefined, progress: number | null | undefined): number {
  if (typeof progress === "number" && progress > 0) return Math.min(100, Math.round(progress <= 1 ? progress * 100 : progress));
  return Math.round(((stageIndex(stage) + 1) / CRAG_STAGES.length) * 100);
}

/** Page locator of a citation: ``pp. 3–5`` / ``pp. 3`` / null when unknown. */
export function pageLabel(ev: Pick<Evidence, "page" | "page_end">): string | null {
  const p = ev.page != null && Number.isFinite(Number(ev.page)) ? Number(ev.page) : null;
  if (p == null) return null;
  const e = ev.page_end != null && Number.isFinite(Number(ev.page_end)) ? Number(ev.page_end) : null;
  return e != null && e > p ? `pp. ${p}–${e}` : `pp. ${p}`;
}

/** Where a citation opens: KB documents in the in-app source viewer, external rows at their OA PDF / landing page. */
export function citationTarget(ev: Evidence): { internal: string } | { external: string } | null {
  const kb = /^kb:([^#]+)/.exec(ev.identifier || "");
  if (kb) return { internal: `/knowledge/sources/${encodeURIComponent(kb[1])}` };
  const href = ev.oa_pdf_url || ev.url || ev.url_alt;
  return href && /^https?:/i.test(href) ? { external: href } : null;
}

/** Last *n* finished turns sent as chat history. */
export function chatHistory(turns: ChatTurn[], n = 6) {
  return turns.filter((m) => !m.streaming).slice(-n).map((m) => ({ role: m.role, content: m.content, citations: m.citations }));
}

const SEVERITY_ORDER: Record<string, number> = { blocking: 0, major: 1, minor: 2, info: 3 };

/** Open findings first, then by severity. */
export function sortFindings(findings: PreflightFinding[]): PreflightFinding[] {
  const rank = (f: PreflightFinding) => (f.status === "open" ? 0 : 10) + (SEVERITY_ORDER[f.severity] ?? 5);
  return [...findings].sort((a, b) => rank(a) - rank(b));
}

export const severityColor = (s: string) => (s === "blocking" ? "error" : s === "major" ? "warning" : "default");

/** Keep at most two versions selected for a diff; a third pick drops the oldest. */
export function toggleDiffPick(prev: string[], id: string): string[] {
  if (prev.includes(id)) return prev.filter((v) => v !== id);
  return prev.length >= 2 ? [prev[1], id] : [...prev, id];
}

/** ``prefix_2026-10-16T08-30-00.ext`` with a filesystem-safe prefix (export shelf naming). */
export function shelfFilename(prefix: string, ext: string, now = new Date()): string {
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const safe = prefix.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "export";
  return `${safe}_${stamp}.${ext.replace(/^\./, "")}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Novelty band of an IP analysis: high (> 0.7), partial overlap (> 0.4), else infringement risk. */
export const noveltyBand = (score: number): "high" | "partial" | "risk" => (score > 0.7 ? "high" : score > 0.4 ? "partial" : "risk");

/** The draft as it would be confirmed when embodiment *idx* is selected (its ingredients replace the formulation's). */
export function draftForEmbodiment(draft: EmbodimentDraft, idx: number): EmbodimentDraft {
  const emb = draft.embodiments?.[idx];
  if (!emb) return draft;
  const ingredients = emb.ingredients ?? [];
  return {
    ...draft,
    amount_source: emb.amount_source || draft.amount_source,
    ingredients_detail: ingredients,
    formulation: { ...draft.formulation, ingredients: ingredients.map((i) => ({ name: i.name, role: i.role || "additive", weight_pct: i.weight_pct })) },
  };
}

/** Fulltext drafts confirm through /formulations, SureChEMBL-only drafts through /surechembl. */
export const confirmPath = (origin: string) =>
  origin === "surechembl" ? "/api/surechembl/confirm-example-draft" : "/api/formulations/confirm-embodiment-draft";

/** Filename from a Content-Disposition header, or *fallback*. */
export function dispositionName(header: string | null, fallback: string): string {
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header || "");
  return m?.[1] ? decodeURIComponent(m[1]) : fallback;
}

/** One ``POST /api/kb/sources/resolve`` match: a search hit (by identifier) → the KB document holding its full text. */
export interface KbResolveMatch { identifier: string; source_id: string; title?: string }

/** Body for ``POST /api/kb/sources/resolve``: the rows that are not mapped yet (≤ 500, de-duplicated by identifier). */
export function resolveItems(rows: Evidence[], mapped: Record<string, string | null | undefined>): { identifier: string; url?: string | null; url_alt?: string | null }[] {
  const seen = new Set<string>();
  const out: { identifier: string; url?: string | null; url_alt?: string | null }[] = [];
  for (const e of rows) {
    const k = evidenceKey(e);
    if (!e.identifier || !k || mapped[k] || seen.has(e.identifier) || /^kb:/.test(e.identifier) || e.is_seed_corpus) continue;
    seen.add(e.identifier);
    out.push({ identifier: e.identifier, url: e.url ?? null, url_alt: e.url_alt ?? null });
    if (out.length >= 500) break;
  }
  return out;
}

/**
 * KB source id of each search hit (row key → source id), from the background KB build's per-document progress
 * (``docs[].identifier/source_id``) and/or the resolve endpoint's matches; DOI / patent number (compact ↔ hyphenated
 * ↔ Google Patents URL) / URL aliases all match. Mirrors legacy ``SourcesPanel.resolveSourceId``. Hits that already
 * are KB chunks (``kb:<id>#c…``) map to their own document.
 */
export function mapKbSourceIds(rows: Evidence[], docs: KbIngestDoc[] = [], matches: KbResolveMatch[] = []): Record<string, string> {
  const out: Record<string, string> = {};
  const candidates: { identifier?: string | null; source_id?: string | null }[] = [
    ...docs.filter((d) => d.source_id && d.status !== "failed"),
    ...matches,
  ];
  for (const e of rows) {
    const k = evidenceKey(e);
    if (!k) continue;
    const kb = /^kb:([^#]+)/.exec(e.identifier || "");
    if (kb) { out[k] = kb[1]; continue; }
    const hit = candidates.find((d) => d.source_id && (idsMatch(d.identifier, e.identifier) || idsMatch(d.identifier, e.url) || idsMatch(d.identifier, e.url_alt)));
    if (hit?.source_id) out[k] = hit.source_id;
  }
  return out;
}
