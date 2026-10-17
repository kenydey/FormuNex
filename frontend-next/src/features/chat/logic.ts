/** Pure helpers of the Q&A chat and the shell banners (unit-tested in logic.test.ts). */
import type { ThinkingStep } from "@/api/stream";
import type { ChatMessage, ChatStreamEvent, Evidence, PlatformHealth } from "./types";
import type { DocDepth } from "@/features/knowledge/types";

// ── Citations ──────────────────────────────────────────────────────────────────────────────────────────────────────

export interface CitationAnchor {
  id: string;
  title: string;
  snippet: string;
  url?: string;
  page?: number | null;
  sourceId?: string | null;
}

export interface FootnoteEntry { id: string; content: string; anchor?: CitationAnchor }

const CITATION_MARKER_RE = /\[\^\d+\]/;
const FOOTNOTE_START_RE = /^\[\^\d+\]:\s*/m;

/** Split an answer carrying backend ``[^n]`` markers into body + footnotes; null when it has no markers. */
export function splitCitationMarkdown(content: string): { answer: string; footnotes: string } | null {
  if (!CITATION_MARKER_RE.test(content)) return null;
  const fnStart = content.search(FOOTNOTE_START_RE);
  if (fnStart >= 0) return { answer: content.slice(0, fnStart).trimEnd(), footnotes: content.slice(fnStart).trim() };
  return { answer: content, footnotes: "" };
}

/** KB source id behind an evidence identifier (``kb:<id>#c3`` from chunks, ``<id>#<node>`` from PageIndex). */
export function kbSourceId(ev: Pick<Evidence, "source" | "identifier">): string | null {
  const id = ev.identifier || "";
  if (id.startsWith("kb:")) return id.slice(3).split("#")[0] || null;
  if (ev.source === "kb_pageindex") return id.split("#")[0] || null;
  return null;
}

/** Evidence[] (1-indexed, as the answer's ``[^n]``) → footnote anchors. */
export function evidenceToAnchors(citations?: Evidence[] | null): CitationAnchor[] {
  return (citations ?? []).map((c, i) => {
    const id = String(i + 1);
    const url = c.url || (c.identifier?.startsWith("http") ? c.identifier : undefined);
    return { id, title: c.title || c.identifier || c.source || `#${id}`, snippet: c.snippet || "", url: url || undefined,
      page: c.page ?? null, sourceId: kbSourceId(c) };
  });
}

export function parseFootnotes(footnotes: string, anchors: CitationAnchor[]): FootnoteEntry[] {
  const entries: FootnoteEntry[] = [];
  let currentId: string | null = null;
  let current: string[] = [];
  const flush = () => {
    if (currentId !== null) {
      const id = currentId;
      entries.push({ id, content: current.join("\n").trim(), anchor: anchors.find((a) => a.id === id) });
    }
    currentId = null;
    current = [];
  };
  for (const raw of footnotes.split("\n")) {
    const line = raw.trimEnd();
    const m = /^\[\^(\d+)\]:\s*(.*)$/.exec(line);
    if (m) {
      flush();
      currentId = m[1];
      current = m[2] ? [m[2]] : [];
    } else if (currentId !== null) {
      if (!line.trim()) flush(); // a blank line ends the footnote
      else current.push(line);
    }
  }
  flush();
  return entries;
}

export const PH_OPEN = "";
export const PH_CLOSE = "";

/**
 * Replace ``[^n]`` outside code (fenced, indented, inline) with private-use placeholders so the Markdown parser leaves
 * them alone; ``rehypeCitationRefs`` turns them back into superscript links.
 */
export function maskCitationMarkers(answer: string): { text: string; ids: string[] } {
  const ids: string[] = [];
  const maskOne = (s: string) => s.replace(/\[\^(\d+)\]/g, (_m, id: string) => {
    ids.push(id);
    return `${PH_OPEN}${ids.length - 1}${PH_CLOSE}`;
  });
  const maskInline = (line: string) => {
    const parts: string[] = [];
    const re = /(`+)[^`\n]*?\1/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line))) {
      parts.push(maskOne(line.slice(last, m.index)), m[0]);
      last = m.index + m[0].length;
    }
    parts.push(maskOne(line.slice(last)));
    return parts.join("");
  };
  const OPEN = /^(\s*)(`{3,}|~{3,})(.*)$/;
  const CLOSE = /^(\s*)(`{3,}|~{3,})\s*$/;
  const out: string[] = [];
  let fenceChar: string | null = null;
  let fenceLen = 0;
  let prevBlank = true;
  let prevIndent = false;
  for (const line of answer.split("\n")) {
    if (fenceChar) {
      out.push(line);
      const m = CLOSE.exec(line);
      if (m && m[2][0] === fenceChar && m[2].length >= fenceLen) fenceChar = null;
      prevBlank = prevIndent = false;
      continue;
    }
    const open = OPEN.exec(line);
    if (open) {
      fenceChar = open[2][0];
      fenceLen = open[2].length;
      out.push(line);
      prevBlank = prevIndent = false;
      continue;
    }
    if (/^(?: {4}|\t)/.test(line) && (prevBlank || prevIndent) && line.trim()) {
      out.push(line);
      prevBlank = false;
      prevIndent = true;
      continue;
    }
    prevIndent = false;
    prevBlank = line.trim() === "";
    out.push(maskInline(line));
  }
  return { text: out.join("\n"), ids };
}

interface HastNode { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HastNode[] }

/** rehype attacher: placeholders → ``<sup><a href="#fn-n">[n]</a></sup>`` (skipping pre/code). */
export function rehypeCitationRefs(ids: string[]): () => (tree: HastNode) => void {
  const tokenRe = new RegExp(`(${PH_OPEN}\\d+${PH_CLOSE})`, "g");
  const singleRe = new RegExp(`^${PH_OPEN}(\\d+)${PH_CLOSE}$`);
  const walk = (node: HastNode): void => {
    if (node.type === "element" && (node.tagName === "pre" || node.tagName === "code")) return;
    if (!Array.isArray(node.children)) return;
    const next: HastNode[] = [];
    for (const child of node.children) {
      if (child.type === "text" && typeof child.value === "string") {
        const parts = child.value.split(tokenRe);
        if (parts.length === 1) {
          next.push(child);
          continue;
        }
        for (const part of parts) {
          const m = singleRe.exec(part);
          if (m) {
            const refId = ids[Number(m[1])];
            next.push({ type: "element", tagName: "sup", properties: { className: ["fx-cite"] }, children: [
              { type: "element", tagName: "a", properties: { href: `#fn-${refId}`, className: ["fx-cite-link"], dataRef: refId },
                children: [{ type: "text", value: `[${refId}]` }] },
            ] });
          } else if (part) next.push({ type: "text", value: part });
        }
      } else {
        walk(child);
        next.push(child);
      }
    }
    node.children = next;
  };
  return () => (tree: HastNode) => walk(tree);
}

// ── Composer tokens ────────────────────────────────────────────────────────────────────────────────────────────────

/** Trailing ``/skill`` or ``@source`` token being typed: the picker filters on ``query``; ``before`` is the rest. */
export function trailingToken(draft: string, prefix: "/" | "@"): { active: boolean; query: string; before: string } {
  const re = prefix === "/" ? /(^|\s)\/([a-zA-Z0-9_-]*)$/ : /(^|\s)@([^\s@]*)$/;
  const m = draft.match(re);
  if (!m) return { active: false, query: "", before: draft };
  const token = m[2] || "";
  return { active: true, query: token.toLowerCase(), before: draft.slice(0, draft.length - token.length - 1) };
}

// ── Stream reducer ─────────────────────────────────────────────────────────────────────────────────────────────────

const closeRunning = (steps: ThinkingStep[], to: "done" | "error"): ThinkingStep[] =>
  steps.map((s) => (s.status === "running" ? { ...s, status: to } : s));

function upsert(steps: ThinkingStep[], step: ThinkingStep): ThinkingStep[] {
  const i = steps.findIndex((s) => s.id === step.id);
  if (i < 0) return [...steps, step];
  const copy = steps.slice();
  copy[i] = { ...copy[i], ...step };
  return copy;
}

/**
 * Apply one SSE frame to the streaming assistant message. Steps hold phase ids as titles (``retrieval``, ``tools``,
 * ``answering``, ``claims``) — the UI translates them — and tool labels for tool calls.
 */
export function applyChatEvent(m: ChatMessage, ev: ChatStreamEvent): ChatMessage {
  const steps = m.steps ?? [];
  switch (ev.type) {
    case "phase": {
      const next = upsert(closeRunning(steps, "done"), { id: `phase:${ev.phase}`, kind: "stage", title: ev.phase, status: "running" });
      return { ...m, phase: ev.phase, toolStatus: ev.phase === "answering" || ev.phase === "claims" ? null : m.toolStatus, steps: next };
    }
    case "meta":
      return { ...m, kbChunksUsed: ev.kb_used, sourceCount: ev.source_count, rewrittenQuery: ev.rewritten_query ?? null };
    case "tool_start": {
      const id = `tool:${ev.name}:${steps.filter((s) => s.kind === "tool").length}`;
      return { ...m, phase: "tools", toolStatus: ev.label || ev.name,
        steps: [...steps, { id, kind: "tool", title: ev.label || ev.name, status: "running" }] };
    }
    case "tool_result": {
      const idx = [...steps].reverse().findIndex((s) => s.kind === "tool" && s.status === "running" && s.id.startsWith(`tool:${ev.name}:`));
      const next = steps.slice();
      if (idx >= 0) {
        const i = steps.length - 1 - idx;
        next[i] = { ...next[i], status: ev.ok ? "done" : "error", detail: ev.summary };
      }
      return { ...m, steps: next, toolStatus: ev.ok ? m.toolStatus : `✕ ${ev.name}` };
    }
    case "token": {
      const hasAnswering = steps.some((s) => s.id === "phase:answering");
      const next = hasAnswering ? steps : upsert(closeRunning(steps, "done"), { id: "phase:answering", kind: "stage", title: "answering", status: "running" });
      return { ...m, content: m.content + ev.delta, steps: next };
    }
    case "step":
      return { ...m, steps: upsert(steps, { id: ev.id, kind: "stage", title: ev.title, status: ev.status, detail: ev.detail }) };
    case "done": {
      const perm = ev.mcp_permission_required;
      return {
        ...m,
        content: ev.answer ?? m.content,
        streaming: false,
        phase: undefined,
        toolStatus: null,
        steps: closeRunning(steps, "done"),
        citations: ev.citations ?? [],
        kbChunksUsed: ev.kb_chunks_used ?? m.kbChunksUsed ?? 0,
        rewrittenQuery: ev.rewritten_query ?? m.rewrittenQuery ?? null,
        reviewRunId: ev.reviewer_fix && typeof ev.reviewer_fix === "object" ? ev.reviewer_fix.run_id ?? null : null,
        sourcedClaims: Array.isArray(ev.sourced_claims) ? ev.sourced_claims : null,
        clarification: ev.clarification && typeof ev.clarification === "object" ? ev.clarification : null,
        evidenceProvenance: ev.evidence_provenance && typeof ev.evidence_provenance === "object" ? ev.evidence_provenance : null,
        notices: Array.isArray(ev.notices) ? ev.notices : null,
        mcpPermission: perm?.server_id && perm.tool_name ? perm : null,
      };
    }
    case "error":
      return { ...m, streaming: false, phase: undefined, toolStatus: null, steps: closeRunning(steps, "error"), error: ev.message };
    default:
      return m;
  }
}

/** Last turns sent as ``history`` (backend: ≤ 12 turns, ≤ 8000 chars each). */
export function historyForRequest(messages: ChatMessage[], max = 6) {
  return messages
    .filter((m) => !m.streaming && m.content && !m.error)
    .slice(-max)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 8000), citations: m.citations ?? [] }));
}

/** What /api/session/save stores (dropping the in-flight placeholder). */
export function serializeHistory(messages: ChatMessage[]): Record<string, unknown>[] {
  return messages.filter((m) => !m.streaming && m.content).map((m) => {
    const { streaming: _s, phase: _p, toolStatus: _t, ...rest } = m;
    void _s; void _p; void _t;
    return rest as unknown as Record<string, unknown>;
  });
}

let seq = 0;
export const newId = (prefix = "m") => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Messages of a loaded session (tolerates legacy rows without ids). */
export function restoreHistory(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && typeof (r as { content?: unknown }).content === "string"
      && ((r as { role?: unknown }).role === "user" || (r as { role?: unknown }).role === "assistant"))
    .map((r) => ({ ...(r as unknown as ChatMessage), id: typeof r.id === "string" ? r.id : newId(), streaming: false }));
}

export function sessionTitle(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === "user")?.content.trim() ?? "";
  return first.slice(0, 80);
}

// ── Deep mode (PageIndex) ──────────────────────────────────────────────────────────────────────────────────────────

/** Stage-2 PageIndex reads as page-anchored Evidence — client-side mirror of ``pageindex.two_stage.navigation_evidence``. */
export function navigationEvidence(docs: DocDepth[], snippetChars = 1600): Evidence[] {
  const out: Evidence[] = [];
  for (const d of docs) {
    if (!d.navigation) continue;
    const n = d.navigation.evidence.length;
    d.navigation.evidence.forEach((ev, i) => {
      const pr = ev.page_range ?? [null, null];
      out.push({
        source: "kb_pageindex",
        identifier: `${d.source_id}#${ev.node_id}`,
        title: `${d.source_id} · ${ev.title || ""}`.replace(/[\s·]+$/, ""),
        snippet: (ev.text || "").slice(0, snippetChars) || ev.title || d.source_id,
        relevance: Math.round(Math.max(0.3, 0.9 - (0.6 * i) / Math.max(1, n)) * 1000) / 1000,
        page: pr[0], page_end: pr[1] !== pr[0] ? pr[1] : null,
      });
    });
  }
  return out.slice(0, 50);
}

// ── Banners ────────────────────────────────────────────────────────────────────────────────────────────────────────

export type InfraIssue = { code: "database" | "datalab" | "broker" | "pdf" | "generic"; hint?: string };

/** /health → none | local (soft-degraded ELN ledger) | degraded (with reasons). */
export function infraState(h: PlatformHealth | null | undefined): { level: "none" | "local" | "degraded"; issues: InfraIssue[]; hint?: string } {
  if (!h) return { level: "none", issues: [] };
  if (h.status === "ok") {
    const local = h.datalab?.ledger_mode === "local" && !h.datalab?.reachable && !h.datalab?.required;
    return local ? { level: "local", issues: [], hint: h.datalab?.hint } : { level: "none", issues: [] };
  }
  const issues: InfraIssue[] = [];
  if (h.database && !h.database.ok) issues.push({ code: "database" });
  if (h.datalab?.required && !h.datalab.reachable) issues.push({ code: "datalab", hint: h.datalab.hint });
  if (h.task_broker?.required && !h.task_broker.reachable) issues.push({ code: "broker" });
  if (h.parsers && h.parsers.pdf === false) issues.push({ code: "pdf" });
  if (!issues.length) issues.push({ code: "generic" });
  return { level: "degraded", issues };
}

/** /health unreachable is itself a degraded state. */
export const UNREACHABLE_HEALTH: PlatformHealth = {
  status: "degraded", database: { ok: false, scheme: "unknown" }, task_broker: { required: true, reachable: false }, parsers: {},
  datalab: { required: false, reachable: false, ledger_mode: "local" },
};

export type DegradedReason = "llm_key" | "online_deps";

export function degradedReasons(settings: { key_set?: boolean } | null | undefined,
  status: Record<string, { available?: boolean }> | null | undefined): DegradedReason[] {
  const out: DegradedReason[] = [];
  if (settings && settings.key_set === false) out.push("llm_key");
  if (status && ["literature", "internet"].some((k) => status[k] && !status[k].available)) out.push("online_deps");
  return out;
}

// ── Tasks ──────────────────────────────────────────────────────────────────────────────────────────────────────────

/** Progress as a percentage; the backend reports 0..1 but tolerate 0..100. */
export const progressPct = (p: number | null | undefined) => {
  const v = Number(p ?? 0);
  if (!Number.isFinite(v) || v <= 0) return 0;
  return Math.min(100, Math.round(v <= 1 ? v * 100 : v));
};

/** A mutation result that enqueued a background task (HTTP 202 ``AsyncTaskAccepted``). */
export function acceptedTaskId(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const id = (data as { task_id?: unknown }).task_id;
  return typeof id === "string" && id ? id : null;
}
