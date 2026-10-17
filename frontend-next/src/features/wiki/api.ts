import { ApiError, call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import type {
  EnvFlag, KgContradiction, KgRelation, KgResolve, KgSubstitute, WikiDossier, WikiFlagItem, WikiPageDetail, WikiPageItem,
  WikiSearchHit,
} from "./types";

const qs = (o: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return p.toString();
};

export const wikiApi = {
  pages: (projectId: string, kind?: string) =>
    call<{ pages: WikiPageItem[]; total: number }>("GET", `/api/wiki/pages?${qs({ kind, limit: 100, project_id: projectId })}`),
  flags: (projectId: string) => call<{ pages: WikiFlagItem[] }>("GET", `/api/wiki/flags?${qs({ limit: 100, project_id: projectId })}`),
  search: (q: string, projectId: string | null, kind?: string) =>
    call<{ hits: WikiSearchHit[]; total: number; mode: string }>("GET", `/api/wiki/search?${qs({ q, kind, limit: 50, project_id: projectId })}`),
  byPath: (path: string) => call<WikiPageDetail>("GET", `/api/wiki/by-path?${qs({ path })}`),
  dossier: (projectId: string) => call<WikiDossier>("GET", `/api/wiki/dossier/${encodeURIComponent(projectId)}`),
  envFlags: () => call<{ flags: EnvFlag[] }>("GET", "/api/settings/env-flags"),
  catalogPath: (projectId: string | null) => `/api/wiki/catalog?${qs({ format: "md", limit: 500, project_id: projectId })}`,
};

/** Resolve an entity and load its substitutes / contradictions / relations (KgRelationPanel). */
export async function loadKgBundle(q: string) {
  const resolved = await call<KgResolve>("GET", `/api/kg/resolve?${qs({ q })}`);
  const primaryId = resolved.chemicals[0]?.id ?? resolved.trade_products[0]?.id ?? null;
  if (!primaryId) return { resolved, substitutes: null, contradictions: null, relations: null };
  const [substitutes, contradictions, relations] = await Promise.all([
    call<{ substitutes: KgSubstitute[] }>("GET", `/api/kg/discover/substitutes?${qs({ entity_id: primaryId, limit: 5 })}`),
    call<{ contradictions: KgContradiction[] }>("GET", `/api/kg/contradictions?${qs({ entity_id: primaryId })}`),
    call<KgRelation[]>("GET", `/api/kg/relations/${encodeURIComponent(primaryId)}?${qs({ direction: "both", limit: 40 })}`).catch(() => null),
  ]);
  return { resolved, substitutes, contradictions, relations };
}

/** Env flag value: true / false, or null when the flag is unknown / unreadable. */
export function envFlag(flags: EnvFlag[] | undefined, attr: string): boolean | null {
  const f = flags?.find((x) => x.attr === attr);
  return f ? Boolean(f.value) : null;
}

/** Errors caused by a disabled feature flag (shown as a hint, not as a failure). */
export function isFlagGateError(e: unknown, ...attrs: string[]): boolean {
  if (!e) return false;
  const m = errorText(e).toLowerCase();
  return attrs.some((a) => m.includes(a.toLowerCase())) || /未启用|is false/i.test(m);
}

export const isNotFound = (e: unknown) => e instanceof ApiError && e.status === 404;
