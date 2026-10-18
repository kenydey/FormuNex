/** URL helpers for wiki pages: a page path such as ``materials/e51.md`` lives at ``/knowledge/wiki/materials/e51.md``. */

export const WIKI_ROUTE = "/knowledge/wiki";

const enc = (s: string) => encodeURIComponent(s).replace(/\(/g, "%28").replace(/\)/g, "%29");

function dec(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** Router link of a wiki page (each path segment encoded; parentheses too so it is safe inside a Markdown link). */
export function wikiHref(path: string): string {
  return `${WIKI_ROUTE}/${path.split("/").filter(Boolean).map(enc).join("/")}`;
}

/** Inverse of {@link wikiHref} for a router pathname; null when it is not a wiki page URL. */
export function wikiPathFromHref(href: string | undefined | null): string | null {
  if (!href) return null;
  const clean = href.split(/[?#]/)[0];
  if (!clean.startsWith(`${WIKI_ROUTE}/`)) return null;
  const rest = clean.slice(WIKI_ROUTE.length + 1);
  const path = rest.split("/").filter(Boolean).map(dec).join("/");
  return path || null;
}

/** ``themes/project-<id>.md`` → project id of a project dossier page. */
export function dossierProjectId(path: string | null | undefined): string | null {
  const m = (path || "").match(/^themes\/project-(.+)\.md$/i);
  return m?.[1] ?? null;
}

/** System key for theme compilation: norm_key, else the file stem. */
export function themeKey(page: { norm_key?: string | null; path?: string | null }): string | null {
  return page.norm_key || page.path?.split("/").pop()?.replace(/\.md$/i, "") || null;
}
