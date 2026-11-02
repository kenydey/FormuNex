import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Navigate, type RouteObject } from "react-router-dom";
import { router } from "./router";
import { NAV, type NavItem } from "./nav";
import { TabRedirect } from "@/shared/hubTabs";

/**
 * Stage 5 / SR-070: frontend route smoke, blocking in CI through the
 * existing `npm test` (vitest) step — no new infra.
 *
 * Three guards, all static (no browser, no running stack):
 * 1. every nav leaf is a real route (leaf reachability);
 * 2. every redirect target (Navigate / TabRedirect) is a real route;
 * 3. every literal <Link to> target in src is a real route.
 * Dynamic targets (to={variable}) cannot be verified statically and
 * are skipped by construction, not by allowlist.
 */

type El = { type?: unknown; props?: { to?: unknown; children?: unknown } };

function asEl(v: unknown): El | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  if (!("type" in o) || !("props" in o)) return null;
  return o as unknown as El;
}

function joinPath(base: string, rel: string): string {
  if (rel.startsWith("/")) return rel;
  if (base === "/") return `/${rel}`;
  return `${base}/${rel}`;
}

function walkRoutes(
  routes: RouteObject[],
  base: string,
  paths: Set<string>,
  redirects: Set<string>,
): void {
  for (const r of routes) {
    const full = r.index ? base : r.path === undefined ? base : joinPath(base, r.path);
    if (r.path !== "*") paths.add(full);
    // Redirects hide inside elements (directly, or one wrapper deep).
    const stack: unknown[] = [r.element];
    let depth = 0;
    while (stack.length > 0 && depth < 12) {
      const el = asEl(stack.pop());
      depth += 1;
      if (!el) continue;
      if ((el.type === Navigate || el.type === TabRedirect) && typeof el.props?.to === "string") {
        redirects.add(el.props.to);
      }
      const kids = el.props?.children;
      if (Array.isArray(kids)) stack.push(...kids);
      else if (kids !== undefined) stack.push(kids);
    }
    if (r.children) walkRoutes(r.children, full, paths, redirects);
  }
}

function navLeaves(items: NavItem[], out: string[]): void {
  for (const item of items) {
    if (item.children) navLeaves(item.children, out);
    else out.push(item.path);
  }
}

function segments(p: string): string[] {
  return p.split("?")[0].split("#")[0].split("/").filter(Boolean);
}

/** A concrete path matches a route pattern when :params match any segment. */
function matchesPattern(path: string, pattern: string): boolean {
  const a = segments(path);
  const b = segments(pattern);
  if (a.length !== b.length) return false;
  return b.every((seg, i) => seg.startsWith(":") || seg === a[i]);
}

/** A static template prefix matches when it covers a pattern's leading segments. */
function prefixMatches(prefix: string, pattern: string): boolean {
  const a = segments(prefix);
  const b = segments(pattern);
  if (a.length > b.length) return false;
  return a.every((seg, i) => b[i].startsWith(":") || b[i] === seg);
}

function collectLinkTargets(): { literals: string[]; templates: string[] } {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const literals: string[] = [];
  const templates: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const p = join(dir, entry);
      if (statSync(p).isDirectory()) {
        if (entry !== "node_modules") walk(p);
        continue;
      }
      if (!/\.tsx?$/.test(entry) || entry.includes(".test.")) continue;
      const text = readFileSync(p, "utf-8");
      for (const m of text.matchAll(/to="([^"]+)"/g)) literals.push(m[1]);
      for (const m of text.matchAll(/to=\{`([^`]*)\}/g)) {
        const body = m[1];
        const cut = body.indexOf("${");
        templates.push(cut === -1 ? body : body.slice(0, cut));
      }
    }
  };
  walk(root);
  return { literals, templates };
}

describe("route smoke (SR-070)", () => {
  const paths = new Set<string>();
  const redirects = new Set<string>();
  walkRoutes(router.routes, "", paths, redirects);
  const patterns = [...paths];

  it("every nav leaf is a route", () => {
    const leaves: string[] = [];
    navLeaves(NAV, leaves);
    expect(leaves.length).toBeGreaterThan(0);
    const missing = leaves.filter((leaf) => !patterns.some((p) => matchesPattern(leaf, p)));
    expect(missing).toEqual([]);
  });

  it("every redirect target is a route", () => {
    expect(redirects.size).toBeGreaterThan(0);
    const missing = [...redirects].filter((t) => !patterns.some((p) => matchesPattern(t, p)));
    expect(missing).toEqual([]);
  });

  it("every literal Link target is a route", () => {
    const { literals, templates } = collectLinkTargets();
    expect(literals.length).toBeGreaterThan(0);
    const missingLit = literals.filter((t) => !patterns.some((p) => matchesPattern(t, p)));
    expect(missingLit).toEqual([]);
    const missingTpl = templates.filter(
      (t) => t.startsWith("/") && !patterns.some((p) => prefixMatches(t, p)),
    );
    expect(missingTpl).toEqual([]);
  });

  it("no duplicate route paths", () => {
    // The walker records each route once; a duplicate means two route
    // objects claim the same path and only the first ever renders.
    const seen = new Map<string, number>();
    const walk = (routes: RouteObject[], base: string): void => {
      for (const r of routes) {
        const full = r.index ? base : r.path === undefined ? base : joinPath(base, r.path);
        // An index route renders at its parent's path, it does not claim
        // a second one — counting it would false-positive on "/".
        if (r.path !== "*" && !r.index) seen.set(full, (seen.get(full) ?? 0) + 1);
        if (r.children) walk(r.children, full);
      }
    };
    walk(router.routes, "");
    const dupes = [...seen.entries()].filter(([, n]) => n > 1).map(([p]) => p);
    expect(dupes).toEqual([]);
  });
});
