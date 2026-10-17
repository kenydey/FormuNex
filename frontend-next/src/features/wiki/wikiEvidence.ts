/** Wiki pages cited in chat / deep-research answers (``identifier = "wiki:<path>"``). */

export interface WikiCitation {
  source?: string | null;
  identifier?: string | null;
  title?: string | null;
  snippet?: string | null;
}

export interface RelatedWiki { path: string; title: string; kind: string; snippet?: string }

export function isWikiEvidence(ev: Pick<WikiCitation, "source" | "identifier">): boolean {
  return ev.source === "wiki" || (ev.identifier || "").startsWith("wiki:");
}

/** ``wiki:materials/e51.md`` → ``materials/e51.md`` */
export function wikiPathFromIdentifier(identifier: string | null | undefined): string | null {
  const id = identifier || "";
  if (!id.startsWith("wiki:")) return null;
  return id.slice("wiki:".length) || null;
}

export function wikiTitleFromEvidence(ev: WikiCitation): string {
  const t = (ev.title || "").trim();
  const m = t.match(/^\[Wiki\/[^\]]+\]\s*(.+)$/i);
  return (m?.[1] || t || wikiPathFromIdentifier(ev.identifier) || "Wiki").trim();
}

export function relatedWikiFromCitations(citations?: WikiCitation[] | null): RelatedWiki[] {
  if (!citations?.length) return [];
  const out: RelatedWiki[] = [];
  const seen = new Set<string>();
  for (const c of citations) {
    if (!isWikiEvidence(c)) continue;
    const path = wikiPathFromIdentifier(c.identifier);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    const kindMatch = (c.title || "").match(/^\[Wiki\/([^\]]+)\]/i);
    const snip = (c.snippet || "").replace(/\s+/g, " ").trim();
    out.push({ path, title: wikiTitleFromEvidence(c), kind: kindMatch?.[1] || "page", snippet: snip ? snip.slice(0, 80) : undefined });
  }
  return out;
}
