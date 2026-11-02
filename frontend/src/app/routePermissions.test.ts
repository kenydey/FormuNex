import { describe, expect, it } from "vitest";
import { NAV, visibleNav } from "./nav";
import { ROUTE_PERMISSIONS, canAccessRoute, routePermissionFor } from "./routePermissions";

const ALL_PERMS = [
  "admin.users", "admin.companies", "audit.read", "admin.llm", "admin.connectors",
  "formulation.read", "knowledge.read", "regulatory.read",
];

const leaves = () => NAV.flatMap((g) => g.children ?? []);

describe("routePermissions (stage 4 / SR-056, single source)", () => {
  it("table holds exactly the previously separate declarations, nothing added or dropped", () => {
    expect(ROUTE_PERMISSIONS).toEqual({
      "/admin/users": { permission: "admin.users" },
      "/admin/companies": { permission: "admin.companies" },
      "/admin/audit": { permission: "audit.read" },
      "/admin/settings": { permissions: ["admin.llm", "admin.connectors"] },
      "/admin/org-dashboard": { permissions: ["formulation.read"] },
      "/knowledge/search": { permission: "knowledge.read" },
      "/plm/regulatory/review-items": { permission: "regulatory.read" },
    });
  });

  it("every nav leaf's permission fields equal the table lookup (derivation is faithful)", () => {
    for (const leaf of leaves()) {
      const perm = routePermissionFor(leaf.path);
      expect(leaf.permission).toBe(perm?.permission);
      expect(leaf.permissions).toEqual(perm?.permissions);
    }
  });

  it("nav visibility and the router guard agree on every leaf's permission dimension, for every permission set", () => {
    // `when` (e.g. desktop-only leaves) is a nav-only presentation
    // dimension, outside SR-056: it is factored in here so the assertion
    // pins exactly the permission agreement, not the when filtering.
    const scenarios: string[][] = [
      [], ...ALL_PERMS.map((p) => [p]),
      ["admin.llm"], ["admin.connectors"], ALL_PERMS,
    ];
    for (const held of scenarios) {
      const can = (p: string) => held.includes(p);
      const visible = new Set(visibleNav(can).flatMap((g) => g.children ?? []).map((l) => l.path));
      for (const leaf of leaves()) {
        const whenOk = !leaf.when || leaf.when();
        expect(visible.has(leaf.path), `${leaf.path} with [${held}]`).toBe(
          canAccessRoute(leaf.path, can) && whenOk,
        );
      }
    }
  });

  it("matching: exact, segment-prefix for tabs, boundary-safe, open otherwise", () => {
    expect(routePermissionFor("/admin/settings")).toEqual({ permissions: ["admin.llm", "admin.connectors"] });
    expect(routePermissionFor("/admin/settings/model")).toEqual({ permissions: ["admin.llm", "admin.connectors"] });
    expect(routePermissionFor("/admin/settingsx")).toBeUndefined();
    expect(routePermissionFor("/dashboard")).toBeUndefined();
    expect(routePermissionFor("/plm/products/123")).toBeUndefined();
    expect(routePermissionFor("/unknown/path")).toBeUndefined();
    expect(canAccessRoute("/admin/settings/connectors", (p) => p === "admin.connectors")).toBe(true);
    expect(canAccessRoute("/admin/settings/connectors", (p) => p === "admin.users")).toBe(false);
  });

  it("tab-scoped gates stay out of the table (route stays open, tab keeps its own gate)", () => {
    expect(routePermissionFor("/lab/measurements")).toBeUndefined();
  });
});
