/**
 * Project-scoped knowledge "materials" table (legacy knowledge-hub/useHubMaterialRows + utils/patentIds):
 * merges the project's session evidence (workspace.sources) with its persisted KB source documents.
 */

const OFFICES = "CN|US|EP|WO|JP|KR|DE|GB|FR";

export function normalizePatentPub(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let s = String(raw).trim().split("?")[0].split("#")[0];
  if (s.toUpperCase().includes("PATENT/")) {
    const parts = s.split("/");
    s = parts[parts.length - 1] || s;
  }
  const compact = s.toUpperCase().replace(/[\s\-_/]/g, "");
  const m = compact.match(new RegExp(`^(${OFFICES})([A-Z]?)(\\d{4,}[A-Z0-9]*)$`, "i"));
  if (m) return `${m[1].toUpperCase()}${(m[2] || "").toUpperCase()}${m[3].toUpperCase()}`;
  const m2 = compact.match(new RegExp(`(${OFFICES})[A-Z]?\\d{4,}[A-Z0-9]*`, "i"));
  return m2 ? m2[0].toUpperCase() : null;
}

function hyphenatedScpn(compact: string | null): string | null {
  if (!compact) return null;
  const m = compact.toUpperCase().match(/^([A-Z]{2})(\d+)([A-Z]\d*)?$/);
  if (!m) return null;
  return m[3] ? `${m[1]}-${m[2]}-${m[3]}` : `${m[1]}-${m[2]}`;
}

/** All origin_url keys that resolve to the same SourceDocument (mirror of backend patent_ids.py). */
export function patentIdAliases(raw: string | null | undefined): string[] {
  const out: string[] = [];
  const push = (v: string | null | undefined) => {
    const k = (v || "").trim();
    if (k && !out.includes(k)) out.push(k.slice(0, 1024));
  };
  push(raw);
  const compact = normalizePatentPub(raw);
  push(compact);
  push(hyphenatedScpn(compact));
  push(compact ? `https://patents.google.com/patent/${compact}` : null);
  return out;
}

export function idsMatch(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  const aa = new Set(patentIdAliases(a));
  return patentIdAliases(b).some((x) => aa.has(x));
}

export interface Evidence {
  source: string;
  identifier: string;
  title?: string;
  snippet?: string;
  relevance?: number;
  url?: string | null;
  url_alt?: string | null;
  oa_pdf_url?: string | null;
  is_oa?: boolean | null;
  assignee?: string | null;
  pub_date?: string | null;
  [k: string]: unknown;
}

export interface KbSource {
  id: string;
  title: string;
  filename: string;
  source_kind: string;
  origin_url?: string | null;
  project_id?: string | null;
  raw_text_chars: number;
  extraction_status: string;
  archived?: boolean;
}

export interface SourceRow {
  row_key: string;
  kind: "session" | "kb";
  title: string;
  source: string;
  identifier: string;
  url?: string | null;
  kb_status?: string;
  source_id: string | null;
  archived: boolean;
  selected: boolean;
  evidence?: Evidence;
}

export function resolveOpenUrl(row: { evidence?: Evidence; url?: string | null }): string | null {
  for (const u of [row.evidence?.oa_pdf_url, row.url, row.evidence?.url_alt]) {
    const s = (u || "").trim();
    if (s.startsWith("http://") || s.startsWith("https://")) return s;
  }
  return null;
}

/** Workspace ``sources`` entries that look like Evidence (the payload may hold legacy junk). */
export function parseEvidence(raw: unknown): Evidence[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((e): e is Evidence => !!e && typeof e === "object" && typeof (e as Evidence).identifier === "string");
}

/**
 * Session rows only appear when the evidence is in this project's KB (project-scoped hub); KB documents that no
 * session row covers are listed on their own. KB rows first, then by title; optional case-insensitive filter.
 */
export function mergeSourceRows(evidence: Evidence[], selected: string[], kbDocs: KbSource[], filter = ""): SourceRow[] {
  const sel = new Set(selected);
  const covered = new Set<string>();
  const rows: SourceRow[] = [];
  for (const e of evidence) {
    const hit = kbDocs.find((d) => idsMatch(d.origin_url, e.identifier) || idsMatch(d.origin_url, e.url));
    if (!hit) continue;
    covered.add(hit.id);
    rows.push({
      row_key: `ev:${e.identifier}`, kind: "kb", title: e.title || e.identifier, source: e.source || "unknown", identifier: e.identifier,
      url: e.url, kb_status: hit.archived ? "archived" : hit.extraction_status || "indexed", source_id: hit.id, archived: !!hit.archived,
      selected: sel.has(e.identifier), evidence: e,
    });
  }
  for (const d of kbDocs) {
    if (covered.has(d.id)) continue;
    rows.push({
      row_key: `kb:${d.id}`, kind: "kb", title: d.title || d.filename || d.id, source: d.source_kind || "kb", identifier: d.origin_url || d.id,
      url: d.origin_url, kb_status: d.archived ? "archived" : d.extraction_status || "indexed", source_id: d.id, archived: !!d.archived, selected: false,
    });
  }
  rows.sort((a, b) => (a.evidence ? 0 : 1) - (b.evidence ? 0 : 1) || a.title.localeCompare(b.title));
  const q = filter.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) => [r.title, r.identifier, r.source, r.url ?? ""].some((x) => x.toLowerCase().includes(q)));
}

export function toggleSelected(selected: string[], identifier: string): string[] {
  return selected.includes(identifier) ? selected.filter((x) => x !== identifier) : [...selected, identifier];
}

/**
 * ``PUT /api/projects/{id}`` body that drops one item from the session evidence. The backend refuses to replace a
 * non-empty ``sources`` list with an empty one (safety net against accidental wipes), so removing the LAST item
 * sends the explicit ``clear_sources`` flag.
 */
export function removeEvidenceBody(evidence: Evidence[], selected: string[], identifier: string): {
  workspace: { sources: Evidence[]; selected_sources: string[] }; clear_sources?: true;
} {
  const sources = evidence.filter((e) => e.identifier !== identifier);
  const body = { workspace: { sources, selected_sources: selected.filter((x) => x !== identifier) } };
  return sources.length === 0 && evidence.length > 0 ? { ...body, clear_sources: true } : body;
}
