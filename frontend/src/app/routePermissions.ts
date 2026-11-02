/**
 * Stage 4 / SR-056: the single source for frontend route permissions.
 *
 * Before this, the same gate lived in three places: nav visibility
 * (nav.tsx / feature module nav), page-level <Forbidden> checks, and
 * nothing at the router (which only checked login state). Now the table
 * below is the only declaration:
 *
 * - the router guard (router.tsx RequirePermission) forbids direct URL
 *   visits without the permission, rendering <Forbidden>;
 * - nav.tsx derives each leaf's permission/permissions from this table,
 *   so a leaf is hidden exactly when the router would forbid it;
 * - page-level <Forbidden> checks stay untouched as defense in depth.
 *
 * Entries come from the previously separate declarations, nothing added
 * and nothing dropped: the five nav gates (admin.*) plus the two
 * page-only gates (knowledge.read on the search hub, regulatory.read on
 * the review queue). Tab-scoped gates (e.g. lab.review on the
 * measurements review tab) are deliberately NOT here: the route itself
 * stays open and the tab keeps its own gate.
 */
export interface RoutePermission {
  /** Single permission required. */
  permission?: string;
  /** Any-of gate (W1-6/D2 semantics): visible/allowed with any listed permission. */
  permissions?: string[];
}

export const ROUTE_PERMISSIONS: Record<string, RoutePermission> = {
  "/admin/users": { permission: "admin.users" },
  "/admin/companies": { permission: "admin.companies" },
  "/admin/audit": { permission: "audit.read" },
  "/admin/settings": { permissions: ["admin.llm", "admin.connectors"] },
  "/admin/org-dashboard": { permissions: ["formulation.read"] },
  "/knowledge/search": { permission: "knowledge.read" },
  "/plm/regulatory/review-items": { permission: "regulatory.read" },
};

/**
 * Longest key that equals the pathname or is a path-segment prefix of it
 * (so /admin/settings also covers /admin/settings/:tab). Returns
 * undefined for open routes, redirects (guarded again after they land),
 * the index, and unknown paths.
 */
export function routePermissionFor(pathname: string): RoutePermission | undefined {
  let best: { key: string; perm: RoutePermission } | undefined;
  for (const [key, perm] of Object.entries(ROUTE_PERMISSIONS)) {
    if (pathname === key || pathname.startsWith(`${key}/`)) {
      if (!best || key.length > best.key.length) best = { key, perm };
    }
  }
  return best?.perm;
}

/** Open when no table entry, else the single permission or any-of list. */
export function canAccessRoute(pathname: string, can: (p: string) => boolean): boolean {
  const perm = routePermissionFor(pathname);
  if (!perm) return true;
  if (perm.permission) return can(perm.permission);
  if (perm.permissions) return perm.permissions.some(can);
  return true;
}
