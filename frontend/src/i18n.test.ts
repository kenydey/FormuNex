import { describe, expect, it } from "vitest";
// @ts-expect-error plain ESM helper without types
import { extract } from "../scripts/i18n-keys.mjs";
import en from "./locales/en-US";
import { NAV, type NavItem } from "./app/nav";
import { STATUS_ZH } from "./shared/tags";

const navKeys = (items: NavItem[]): string[] => items.flatMap((i) => [i.key, ...navKeys(i.children ?? [])]);

describe("i18n", () => {
  it("every static key in the code has an English string", () => {
    const keys = [...(extract("src") as Map<string, string>).keys()];
    expect(keys.length).toBeGreaterThan(400);
    expect(keys.filter((k) => !(k in en))).toEqual([]);
  });

  it("dynamic keys (navigation, statuses) are translated", () => {
    expect(navKeys(NAV).filter((k) => !(k in en))).toEqual([]);
    expect(Object.keys(STATUS_ZH).map((s) => `status.${s}`).filter((k) => !(k in en))).toEqual([]);
  });

  it("dynamic key families have English strings", () => {
    // Stage 5 / SR-073: the static extractor cannot see template keys
    // like t(`rheo.${assessment}`), so each dynamic family gets an
    // explicit member table here, verified against its producer at
    // SR-073 time. A missing string fails here instead of surfacing
    // as a raw code in the UI (the rheo family was exactly that bug:
    // shear_thinning / newtonian / shear_thickening showed untranslated
    // in both languages). Limitation, stated not hidden: a producer
    // value added later still needs a table row — the extractor gap
    // for brand-new values remains.
    const families: Record<string, string[]> = {
      // Backend rheology assessments (services/rheology.py).
      rheo: ["shear_thinning", "newtonian", "shear_thickening"],
      // Backend RBAC roles (me.roles, sign-off flow steps).
      role: ["sys_admin", "project_lead", "rd_engineer", "ehs_regulatory", "lab_technician", "quality", "release_manager"],
      // SourceBadge color map (shared/tags.tsx SOURCE_COLORS).
      source: ["computed", "measured", "manual", "template", "product", "company", "batch", "legacy", "supplier_sds", "cn_catalog", "pubchem", "inferred"],
      // StructuredDiff kinds (shared/StructuredDiff.tsx) + static labels.
      diff: ["added", "removed", "changed", "field", "kind", "major", "new", "old"],
      // Label element types (features/plm/labelLayout.ts ELEMENT_NAMES).
      "label.el": ["product_name", "ingredients", "pictograms", "signal_word", "hazard_statements", "precautionary_statements", "supplier", "emergency_phone", "sds_reference", "qr", "text"],
      // Application methods (features/spec/SpecStudioPage.tsx METHODS).
      "spec.method": ["airless", "air_spray", "electrostatic", "dip", "roller", "brush", "electrophoretic", "autodeposition"],
      // Group-home section ids (app/GroupHome.tsx).
      "ia.sub": ["knowledge", "qa", "research", "search"],
      // Group-home next-step hints (app/GroupHome.tsx NEXT_HINTS).
      "ia.next": ["cockpit", "data", "lab", "materials", "product", "spec"],
    };
    const missing = Object.entries(families).flatMap(([family, members]) =>
      members.map((m) => `${family}.${m}`).filter((k) => !(k in en)),
    );
    // Group subtitles are rendered as t(`${group.key}.desc`) in GroupHome.
    const groupDescs = NAV.map((g) => `${g.key}.desc`).filter((k) => !(k in en));
    expect([...missing, ...groupDescs]).toEqual([]);
  });
});
