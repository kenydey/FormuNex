/** [[wikilink]] resolve + rewrite (S1 + P2 ``kind:key`` syntax). Live links become router links, dead ones ``#wiki-dead:``. */
import { wikiHref, wikiPathFromHref } from "./paths";

export type WikiLinkIndexEntry = { path: string; title: string; norm_key?: string; kind?: string };

export type WikiLinkIndex = {
  byPath: Map<string, WikiLinkIndexEntry>;
  byTitle: Map<string, WikiLinkIndexEntry>;
  byNorm: Map<string, WikiLinkIndexEntry>;
  byKindKey: Map<string, WikiLinkIndexEntry>;
};

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
const DEAD = "#wiki-dead:";

export function buildWikiLinkIndex(pages: WikiLinkIndexEntry[]): WikiLinkIndex {
  const byPath = new Map<string, WikiLinkIndexEntry>();
  const byTitle = new Map<string, WikiLinkIndexEntry>();
  const byNorm = new Map<string, WikiLinkIndexEntry>();
  const byKindKey = new Map<string, WikiLinkIndexEntry>();
  for (const p of pages) {
    if (!p.path) continue;
    byPath.set(p.path.toLowerCase(), p);
    const stem = p.path.replace(/\.md$/i, "").toLowerCase();
    if (stem) byPath.set(stem, p);
    if (p.title) byTitle.set(p.title.trim().toLowerCase(), p);
    if (p.norm_key) byNorm.set(p.norm_key.trim().toLowerCase(), p);
    if (p.kind && p.norm_key) byKindKey.set(`${p.kind}:${p.norm_key}`.toLowerCase(), p);
    if (p.kind) {
      const leaf = p.path.split("/").pop()?.replace(/\.md$/i, "") || "";
      if (leaf) byKindKey.set(`${p.kind}:${leaf}`.toLowerCase(), p);
    }
  }
  return { byPath, byTitle, byNorm, byKindKey };
}

export function resolveWikiLink(target: string, index: WikiLinkIndex): WikiLinkIndexEntry | null {
  const t = (target || "").trim();
  if (!t) return null;
  const lower = t.toLowerCase();
  if (lower.includes(":")) {
    const hit = index.byKindKey.get(lower);
    if (hit) return hit;
  }
  return index.byPath.get(lower) || index.byPath.get(lower.replace(/\.md$/i, "")) || index.byTitle.get(lower)
    || index.byNorm.get(lower) || null;
}

export function rewriteWikiLinks(markdown: string, index: WikiLinkIndex): string {
  return (markdown || "").replace(WIKILINK_RE, (_m, target: string, label?: string) => {
    const rawTarget = String(target || "").trim();
    const display = (label || rawTarget).trim() || rawTarget;
    const hit = resolveWikiLink(rawTarget, index);
    if (hit) return `[${display}](${wikiHref(hit.path)})`;
    return `[${display}](${DEAD}${encodeURIComponent(rawTarget).replace(/\(/g, "%28").replace(/\)/g, "%29")})`;
  });
}

export function parseWikiHref(href: string | undefined): { kind: "path" | "dead"; value: string } | null {
  if (!href) return null;
  if (href.startsWith(DEAD)) {
    try {
      return { kind: "dead", value: decodeURIComponent(href.slice(DEAD.length)) };
    } catch {
      return { kind: "dead", value: href.slice(DEAD.length) };
    }
  }
  const path = wikiPathFromHref(href);
  return path ? { kind: "path", value: path } : null;
}

/** Whether a dead-link target uses the ``kind:key`` syntax (the tooltip then points at kind/norm_key). */
export const isKindKeyTarget = (target: string) => (target || "").includes(":");
