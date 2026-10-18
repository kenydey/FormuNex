/** Pure helpers of the settings centre (unit-tested in logic.test.ts). */
import type { DependencyInfo, EnvFlag, LlmModelOption, LlmProviderInfo, Neo4jRawStats, UnifiedSkill } from "./types";

/** Tabs of /admin/settings/:tab. */
export const SETTINGS_TABS = ["model", "keys", "api-token", "capabilities", "env", "deps", "kb"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** Legacy tab ids (FormuMind settings modal, possibly bookmarked) → new tab. */
const LEGACY_TAB_MAP: Record<string, SettingsTab> = {
  llm: "model",
  api: "keys",
  secrets: "keys",
  skills: "capabilities",
  notebooklm: "capabilities",
  advanced: "env",
  maintenance: "kb",
};

export function normalizeSettingsTab(id: string | null | undefined): SettingsTab {
  if (!id) return "model";
  if ((SETTINGS_TABS as readonly string[]).includes(id)) return id as SettingsTab;
  return LEGACY_TAB_MAP[id] ?? "model";
}

/** Group rows by a key, keeping first-seen order of the groups. */
export function groupBy<T>(rows: T[], key: (r: T) => string): [string, T[]][] {
  const map = new Map<string, T[]>();
  for (const r of rows) {
    const k = key(r);
    const list = map.get(k);
    if (list) list.push(r);
    else map.set(k, [r]);
  }
  return [...map.entries()];
}

/** Only the toggles whose draft differs from the server value (POST /api/settings/env-flags body). */
export function envFlagUpdates(flags: EnvFlag[], drafts: Record<string, boolean>): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const f of flags) {
    const d = drafts[f.attr];
    if (d !== undefined && d !== f.value) out[f.attr] = d;
  }
  return out;
}

/** Display order of the optional-dependency extras; unknown extras are listed last. */
export const DEP_EXTRA_ORDER = [
  "llm", "intel", "embedding", "colbert", "crag", "science", "optimize", "bo", "pydoe", "baybe", "color", "file_ingest",
  "parse_pro", "export", "notebooklm",
];

export function groupDependencies(deps: DependencyInfo[]): { extra: string; items: DependencyInfo[] }[] {
  const groups = groupBy(deps, (d) => d.extra);
  const rank = (e: string) => {
    const i = DEP_EXTRA_ORDER.indexOf(e);
    return i < 0 ? DEP_EXTRA_ORDER.length : i;
  };
  return groups.sort((a, b) => rank(a[0]) - rank(b[0])).map(([extra, items]) => ({ extra, items }));
}

/**
 * The ``disabled_ids`` to send when *skill* is switched to *enabled*: every other skill keeps its state,
 * and always-on skills are never listed (the server refuses to disable them).
 */
export function disabledSkillIds(skills: UnifiedSkill[], skillId: string, enabled: boolean): string[] {
  return skills
    .filter((s) => (s.id === skillId ? !enabled : !s.enabled))
    .filter((s) => s.activation_policy !== "always-on")
    .map((s) => s.id);
}

/** A manually entered MCP server, in the Claude-Desktop ``mcpServers`` shape the import endpoint accepts. */
export function manualMcpConfig(id: string, command: string, args: string): Record<string, unknown> {
  return {
    mcpServers: {
      [id.trim() || "mcp"]: { command: command.trim(), args: args.split(/\s+/).map((x) => x.trim()).filter(Boolean) },
    },
  };
}

/** The model to pick for a provider: its recommended entry, else the first one. */
export function recommendedModel(models: LlmModelOption[] | undefined): string {
  return (models?.find((m) => m.recommended) ?? models?.[0])?.id ?? "";
}

/** Model options for a select: the catalog (or refreshed list), else at least the current value. */
export function modelOptions(models: LlmModelOption[] | undefined, current: string): LlmModelOption[] {
  if (models && models.length > 0) return models;
  return current ? [{ id: current, label: current }] : [];
}

export const CUSTOM_PROVIDER = "custom";

/** Text-model form shows a Base URL field for providers with a catalog default, OpenAI-compatible and custom ones. */
export function showsBaseUrl(provider: LlmProviderInfo | undefined, providerId: string): boolean {
  return !!provider?.base_url || providerId === "openai" || providerId === CUSTOM_PROVIDER;
}

/** Large downloads (bge-m3 ≈ 2 GB) ask for confirmation. */
export function isLargeDownload(sizeEstimate: string): boolean {
  return /GB/i.test(sizeEstimate);
}

/** GET /api/kg/neo4j/stats → flat counts (the raw ``stats`` holds per-label / per-relation numbers). */
export function neo4jSummary(raw: Neo4jRawStats): {
  enabled: boolean; reachable: boolean; nodes?: number; edges?: number; compounds?: number; formulations?: number;
} {
  const s = raw.stats ?? {};
  const known = Object.keys(s).length > 0;
  const n = (k: string) => (typeof s[k] === "number" ? s[k] : 0);
  return {
    enabled: raw.enabled !== false,
    reachable: raw.reachable !== false,
    compounds: known ? n("compound") : undefined,
    formulations: known ? n("formulation") : undefined,
    nodes: known ? n("compound") + n("formulation") + n("experimentreport") : undefined,
    edges: known ? n("contains_rels") + n("similar_to_rels") + n("evaluates_rels") : undefined,
  };
}

/** Short SHA for update messages. */
export const shortSha = (sha: string | null | undefined) => (sha ?? "").slice(0, 8);
