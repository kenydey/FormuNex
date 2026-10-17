import { describe, expect, it } from "vitest";
import {
  addConstraint, clearConstraintValue, customConstraints, getConstraintValue, parseActiveConstraints, removeConstraint, removeCustomConstraint,
  resetConstraints, setCustomConstraint,
} from "./constraints";
import type { ConstraintRequirement } from "./constraints";
import { previewEffectTrace, traceCounts } from "./effectTrace";
import { applyLookup, draftFromView, lookupQuery, lookupSource, nextAvailability, payloadFromDraft, structureMode, EMPTY_DRAFT } from "./materials/materialForm";
import { causeParts } from "./ProjectHistory";
import { flattenPaths, resolveStep } from "./paths";
import { idsMatch, mergeSourceRows, normalizePatentPub, parseEvidence, removeEvidenceBody, resolveOpenUrl, toggleSelected, type KbSource } from "./sourceRows";

describe("constraints", () => {
  const base: ConstraintRequirement = { domain: "anticorrosion_coating", voc_limit_gpl: 300, constraint_values: { "VOC 上限": 300, 附着力: 1 } };

  it("reads values with defaults", () => {
    expect(getConstraintValue(base, "voc_limit_gpl")).toBe(300);
    expect(getConstraintValue(base, "cure_temperature_c")).toBe(160);
    expect(getConstraintValue({ domain: "degreaser" }, "ph_target")).toBe(12);
  });

  it("clears nullable fields to null and the rest to 0", () => {
    expect(clearConstraintValue(base, "voc_limit_gpl").voc_limit_gpl).toBeNull();
    expect(clearConstraintValue(base, "salt_spray_hours").salt_spray_hours).toBe(0);
  });

  it("adds / removes catalogue constraints", () => {
    const a = addConstraint(base, ["voc_limit_gpl"], "salt_spray_hours");
    expect(a.keys).toEqual(["voc_limit_gpl", "salt_spray_hours"]);
    expect(a.req.salt_spray_hours).toBe(1500);
    expect(addConstraint(a.req, a.keys, "salt_spray_hours").keys).toHaveLength(2);
    const r = removeConstraint(a.req, a.keys, "voc_limit_gpl");
    expect(r.keys).toEqual(["salt_spray_hours"]);
    expect(r.req.voc_limit_gpl).toBeNull();
  });

  it("resets to domain defaults and clears constraints of other domains", () => {
    const r = resetConstraints<ConstraintRequirement>({ domain: "degreaser", cure_temperature_c: 120, cleaning_efficiency: 90 });
    expect(r.keys).toEqual(["voc_limit_gpl", "ph_target"]);
    expect(r.req.cure_temperature_c).toBeNull();
    expect(r.req.cleaning_efficiency).toBe(90);
    expect(r.req.ph_target).toBe(12);
  });

  it("custom constraints exclude catalogue mirrors", () => {
    expect(customConstraints(base)).toEqual([["附着力", 1]]);
    const s = setCustomConstraint(base, "  硬度 ", 2);
    expect(s.constraint_values).toMatchObject({ 硬度: 2 });
    expect(setCustomConstraint(base, "  ", 2)).toBe(base);
    expect(removeCustomConstraint(s, "附着力").constraint_values).not.toHaveProperty("附着力");
  });

  it("sanitises the persisted active list", () => {
    expect(parseActiveConstraints(["voc_limit_gpl", "bogus", 3])).toEqual(["voc_limit_gpl"]);
    expect(parseActiveConstraints(null)).toEqual([]);
  });
});

describe("effect trace", () => {
  it("lists defaults when objectives / levers are empty", () => {
    const items = previewEffectTrace({ objectives: [], levers: [], voc_limit_gpl: null, notes: " " });
    expect(items.map((i) => i.field)).toEqual(["objectives", "levers"]);
  });
  it("wires objectives, levers, constraints and marks display-only fields", () => {
    const items = previewEffectTrace({
      objectives: [{ metric: "salt_spray_hours" }], levers: [{ name: "resin" }], voc_limit_gpl: 420, cure_temperature_c: 80, ph_target: 7,
      notes: "x", constraint_values: { 硬度: 2, 空: null },
    });
    expect(items.map((i) => i.field)).toEqual([
      "objectives.salt_spray_hours", "levers.resin", "voc_limit_gpl", "cure_temperature_c", "ph_target", "notes", "constraint_values.硬度",
    ]);
    expect(traceCounts(items)).toEqual({ wired: 5, unwired: 0, display_only: 2 });
    expect(previewEffectTrace(null)).toEqual([]);
  });
});

describe("material form", () => {
  const view = { name: "E-51", role: "resin", spec: { cas_no: "25068-38-6", molar_mass: "380", suppliers_json: [{ name: "A", price_cny_per_kg: 20 }, null] } };
  it("round-trips a material view into the upsert payload", () => {
    const d = draftFromView(view);
    expect(d.molar_mass).toBe(380);
    expect(d.suppliers_json).toHaveLength(1);
    const p = payloadFromDraft({ ...d, zh_name: " ", suppliers_json: [...d.suppliers_json, { name: "  " }] });
    expect(p.zh_name).toBeNull();
    expect(p.cas_no).toBe("25068-38-6");
    expect(p.suppliers_json).toEqual([expect.objectContaining({ name: "A", price_cny_per_kg: 20, price_source: "manual", url: null })]);
  });
  it("applies a lookup without overwriting a typed name", () => {
    const r = { query: "x", cas: "64-17-5", iupac_name: "ethanol", zh_name: "乙醇", formula: "C2H6O", smiles: "CCO", molar_mass: 46.07, found: true, source: "pubchem_name",
      suppliers: [{ name: "Sigma", url: "https://s" }] };
    const typed = applyLookup({ ...EMPTY_DRAFT, name: "Ethanol 99" }, r);
    expect(typed.name).toBe("Ethanol 99");
    expect(typed.smiles).toBe("CCO");
    expect(typed.suppliers_json[0]).toMatchObject({ name: "Sigma", url: "https://s" });
    expect(applyLookup(EMPTY_DRAFT, r).name).toBe("ethanol");
    expect(lookupSource(r).kind).toBe("pubchem");
    expect(lookupSource({ source: "surechembl", surechembl: { chemical_id: "1" } })).toMatchObject({ kind: "surechembl", id: "1" });
  });
  it("picks the lookup query, availability cycle and structure mode", () => {
    expect(lookupQuery({ name: "n", cas_no: " 1-1-1 " })).toBe("1-1-1");
    expect(lookupQuery({ smiles: "CCO" })).toBe("CCO");
    expect(["in_stock", "restricted", "discontinued", undefined].map(nextAvailability)).toEqual(["restricted", "discontinued", "in_stock", "restricted"]);
    expect(structureMode("[OH]c")).toBe("substructure");
    expect(structureMode("CC(=O)O")).toBe("scaffold");
    expect(structureMode("c1ccccc1")).toBe("substructure");
  });
});

describe("project sources", () => {
  const kb: KbSource[] = [
    { id: "s1", title: "Patent", filename: "", source_kind: "patent", origin_url: "CN-112345678-A", raw_text_chars: 1, extraction_status: "indexed" },
    { id: "s2", title: "Alpha doc", filename: "a.pdf", source_kind: "upload", origin_url: null, raw_text_chars: 1, extraction_status: "indexed", archived: true },
  ];
  it("normalises patent ids", () => {
    expect(normalizePatentPub("https://patents.google.com/patent/CN112345678A")).toBe("CN112345678A");
    expect(idsMatch("CN112345678A", "CN-112345678-A")).toBe(true);
    expect(idsMatch("CN1", null)).toBe(false);
  });
  it("merges session evidence with KB docs (project-scoped)", () => {
    const ev = parseEvidence([{ source: "patent", identifier: "CN112345678A", title: "Zeta", oa_pdf_url: "https://x/p.pdf" }, { source: "lit", identifier: "doi:1" }, "junk"]);
    expect(ev).toHaveLength(2);
    const rows = mergeSourceRows(ev, ["CN112345678A"], kb);
    expect(rows.map((r) => r.row_key)).toEqual(["ev:CN112345678A", "kb:s2"]);
    expect(rows[0]).toMatchObject({ source_id: "s1", selected: true });
    expect(rows[1]).toMatchObject({ archived: true, kb_status: "archived" });
    expect(resolveOpenUrl(rows[0])).toBe("https://x/p.pdf");
    expect(mergeSourceRows(ev, [], kb, "alpha").map((r) => r.row_key)).toEqual(["kb:s2"]);
    expect(toggleSelected(["a"], "a")).toEqual([]);
    expect(toggleSelected([], "a")).toEqual(["a"]);
  });
});

describe("history & paths", () => {
  it("parses snapshot causes", () => {
    expect(causeParts("update").kind).toBe("update");
    expect(causeParts("rollback-to-3")).toMatchObject({ kind: "rollback", version: 3 });
    expect(causeParts(null).kind).toBe("other");
  });
  it("resolves steps against known routes", () => {
    const known = flattenPaths([{ path: "/spec", children: [{ path: "/spec/projects" }] }, { path: "/materials/raw" }]);
    expect(resolveStep({ id: "r", routes: ["/spec/projects/:projectId", "/spec/projects"] }, known, null)).toBe("/spec/projects");
    expect(resolveStep({ id: "r", routes: ["/spec/projects/:projectId"] }, known, "p 1")).toBe("/spec/projects/p%201");
    expect(resolveStep({ id: "m", routes: ["/materials/raw?tab=structure"] }, known, null)).toBe("/materials/raw?tab=structure");
    expect(resolveStep({ id: "w", routes: ["/knowledge/wiki"] }, known, null)).toBeNull();
  });
});

describe("removeEvidenceBody", () => {
  const ev = [{ source: "patent", identifier: "a", title: "A" }, { source: "patent", identifier: "b", title: "B" }];
  it("drops the item and its selection without the clear flag while items remain", () => {
    expect(removeEvidenceBody(ev, ["a", "b"], "a")).toEqual({ workspace: { sources: [ev[1]], selected_sources: ["b"] } });
  });
  it("sends clear_sources when the last item goes", () => {
    expect(removeEvidenceBody([ev[0]], ["a"], "a")).toEqual({ workspace: { sources: [], selected_sources: [] }, clear_sources: true });
  });
});
