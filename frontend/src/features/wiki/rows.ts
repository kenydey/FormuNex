import { useEffect, useState } from "react";
import type { WikiFlagItem, WikiListRow, WikiPageItem, WikiSearchHit } from "./types";

/**
 * Rows of the wiki browser: flagged pages (flags mode) or all pages; "reviewed only" hides ``unreviewed`` drafts;
 * server search hits (≥ 2 chars) replace the list in rank order, else a local title / path / norm_key filter.
 */
export function wikiRows(opts: {
  pages: WikiPageItem[];
  flagPages?: WikiFlagItem[];
  hits?: WikiSearchHit[] | null;
  flagsOnly: boolean;
  reviewedOnly: boolean;
  query: string;
}): WikiListRow[] {
  const base: WikiListRow[] = opts.flagsOnly ? (opts.flagPages ?? []) : opts.pages;
  const reviewed = (p: WikiListRow) => !opts.reviewedOnly || !(p.flags || []).includes("unreviewed");
  const list = base.filter(reviewed);
  if (!opts.flagsOnly && opts.hits) {
    const byPath = new Map(list.map((p) => [p.path, p]));
    return opts.hits
      .map((h): WikiListRow => ({ ...(byPath.get(h.path) ?? { ...h, id: h.id || h.path, norm_key: h.norm_key || "" }), snippet: h.snippet }))
      .filter(reviewed);
  }
  const q = opts.query.trim().toLowerCase();
  if (!q) return list;
  return list.filter((p) => [p.title, p.path, p.norm_key].some((s) => (s || "").toLowerCase().includes(q)));
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}
