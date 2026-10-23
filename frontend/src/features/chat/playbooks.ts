/**
 * Composer playbooks (legacy ``ChatComposerPlus`` 行动包 + ``ActivePlaybookStrip``). Legacy playbooks opened a modal
 * in the single-page workspace; in the new UI each one is a routed page, so a playbook becomes a navigation entry
 * that carries the current question / project (``?q=`` + location state). Pages with a question field fill it in;
 * the others show it in a ``HandoffBanner`` (shared/handoff.tsx). Playbooks with no page (installed packs with an
 * unknown action) prefill the composer instead.
 */
import type { UnifiedSkill } from "./types";

export interface PlaybookContext { question?: string; projectId?: string | null }

/** Location state handed to the target page; pages that accept a question read ``state.question``. */
export interface PlaybookNavState { question?: string; projectId?: string | null; playbook: string }

export type PlaybookTarget =
  | { kind: "navigate"; to: string; state: PlaybookNavState }
  | { kind: "prefill"; text: string };

/** Built-in entries (always shown) — page-level workflows of the new UI that the chat should be able to hand off to. */
export interface BuiltinPlaybook { id: string; icon: string; path: string; questionParam?: string }

export const BUILTIN_PLAYBOOKS: BuiltinPlaybook[] = [
  { id: "recommend", icon: "⭐", path: "/lab/recommend", questionParam: "q" },
  { id: "doe_classic", icon: "🔬", path: "/lab/doe?tab=classic", questionParam: "q" },
  { id: "loop", icon: "🔄", path: "/cockpit/optimization?tab=loop", questionParam: "q" },
  { id: "inverse_design", icon: "🎯", path: "/lab/inverse-design", questionParam: "q" },
  { id: "substitution", icon: "🔁", path: "/lab/substitution", questionParam: "q" },
  { id: "deep_research", icon: "📑", path: "/knowledge/research?tab=deep", questionParam: "q" },
  { id: "ip_analysis", icon: "⚖️", path: "/knowledge/research/ip", questionParam: "q" },
];

/** Backend playbook ``action`` → built-in entry (``GET /api/skills`` kind=playbook rows). */
const ACTION_TO_BUILTIN: Record<string, string> = {
  recommend: "recommend",
  doe: "doe_classic",
  optimize: "loop",
  loop: "loop",
  deep_research: "deep_research",
  inverse_design: "inverse_design",
  substitution: "substitution",
  ip_analysis: "ip_analysis",
  fto: "ip_analysis",
};

/** Append ``param=question`` to a path that may already carry a query string. */
export function withQuery(path: string, param: string | undefined, value: string | undefined): string {
  const v = (value ?? "").trim();
  if (!param || !v) return path;
  const [base, qs = ""] = path.split("?");
  const p = new URLSearchParams(qs);
  p.set(param, v.slice(0, 500));
  return `${base}?${p.toString()}`;
}

export function builtinTarget(pb: BuiltinPlaybook, ctx: PlaybookContext): PlaybookTarget {
  const question = ctx.question?.trim() || undefined;
  return {
    kind: "navigate",
    to: withQuery(pb.path, pb.questionParam, question),
    state: { question, projectId: ctx.projectId ?? null, playbook: pb.id },
  };
}

/** Where a backend playbook goes: its built-in page, or (unknown action) a prefilled prompt. */
export function playbookTarget(skill: Pick<UnifiedSkill, "id" | "title" | "summary"> & { action?: string }, ctx: PlaybookContext): PlaybookTarget {
  const builtin = BUILTIN_PLAYBOOKS.find((b) => b.id === ACTION_TO_BUILTIN[skill.action ?? ""]);
  if (builtin) {
    const t = builtinTarget(builtin, ctx);
    return t.kind === "navigate" ? { ...t, state: { ...t.state, playbook: skill.id } } : t;
  }
  const head = `【${skill.title}】${skill.summary ? ` ${skill.summary}` : ""}`.trim();
  const q = ctx.question?.trim();
  return { kind: "prefill", text: q ? `${head}\n${q}` : `${head}\n` };
}

/** Built-in entries not already covered by an enabled backend playbook (which then shows under its own title). */
export function uncoveredBuiltins(playbooks: { action?: string }[]): BuiltinPlaybook[] {
  const covered = new Set(playbooks.map((p) => ACTION_TO_BUILTIN[p.action ?? ""]).filter(Boolean));
  return BUILTIN_PLAYBOOKS.filter((b) => !covered.has(b.id));
}
