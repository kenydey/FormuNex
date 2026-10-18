import { describe, expect, it } from "vitest";
import {
  buildRowDiff, casChecksumOk, casStatus, csvCell, datalabSyncOutcome, deepDiffSummary, leaderboardToCsv, lineageTrend, paretoFront,
  planVsActual, previousVersion, shiftAfterRemove, specText, valueMeetsSpec, versionTree, warningActions, weightTotal,
} from "./logic";
import type { FormulationVersion, WorkbenchRow } from "./types";

const row = (id: number, p: Partial<WorkbenchRow> = {}): WorkbenchRow => ({
  id, campaign_id: 1, item_id: `s${id}`, status: "Completed", planned_params: {}, actual_params: {}, measurements: {}, ingested: false, ...p,
});
const ver = (id: string, version: number, parent: string | null): FormulationVersion => ({
  id, lineage_id: "L", version, parent_version_id: parent, name: "F", domain: "d", change_summary: `c${version}`, created_by: "", created_at: null,
});

describe("workbench rows", () => {
  it("diffs factors (actual overrides planned) and measurements", () => {
    const d = buildRowDiff(row(1, { planned_params: { a: 1, b: 2 }, actual_params: { a: 1.5 }, measurements: { m: 3 } }),
      row(2, { planned_params: { a: 1.5, b: 2 }, measurements: { m: 4 } }));
    expect(d.factors).toEqual([{ key: "a", a: 1.5, b: 1.5, changed: false }, { key: "b", a: 2, b: 2, changed: false }]);
    expect(d.measurements[0].changed).toBe(true);
  });
  it("builds the trend root → current with nulls", () => {
    const tr = lineageTrend([row(3, { measurements: { m: 30 } }), row(2, { measurements: {} }), row(1, { measurements: { m: "10" } })], ["m"]);
    expect(tr.rounds).toEqual(["1", "2", "3"]);
    expect(tr.series[0].data).toEqual([10, null, 30]);
  });
  it("flags deviated factors", () => {
    expect(planVsActual(row(1, { planned_params: { a: 1, b: 2 }, actual_params: { a: 1, b: 3 } })).map((x) => x.deviated)).toEqual([false, true]);
  });
});

describe("specs", () => {
  it("formats and judges", () => {
    expect(specText({ metric: "x", ref_min: 1, ref_max: 2, unit: "h" })).toBe("1–2 h");
    expect(specText({ metric: "x", direction: "minimize", target_value: 5 })).toBe("≤ 5");
    expect(valueMeetsSpec({ metric: "x", ref_min: 10 }, 9)).toBe(false);
    expect(valueMeetsSpec({ metric: "x", direction: "maximize", target_value: 10 }, 12)).toBe(true);
    expect(valueMeetsSpec({ metric: "x" }, "")).toBeNull();
  });
});

describe("ingredients", () => {
  it("checks CAS", () => {
    expect(casChecksumOk("7732-18-5")).toBe(true);
    expect(casChecksumOk("7732-18-4")).toBe(false);
    expect(casStatus({ name: "Water", role: "", weight_pct: 1, cas_no: "" }, [])).toBe("missing");
    expect(casStatus({ name: "Water", role: "", weight_pct: 1, cas_no: "7732-18-5" }, ["Water: CAS 不一致，已采用目录值"])).toBe("corrected");
    expect(weightTotal([{ name: "a", role: "", weight_pct: 33.3333 }, { name: "b", role: "", weight_pct: 66.6667 }])).toBe(100);
  });
  it("classifies validate warnings", () => {
    expect(warningActions(["A: missing CAS"])).toEqual({ proposeMissing: true, openMaterials: true, hasCompliance: false });
    expect(warningActions(["B 命中 SVHC"]).hasCompliance).toBe(true);
    expect(warningActions([]).openMaterials).toBe(false);
  });
  it("guards CSV cells", () => {
    expect(csvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
    expect(leaderboardToCsv([{ name: "F", domain: "anticorrosion_coating", ingredients: [], predicted: { salt_spray_hours: 500 }, score: 0.9 }]).split("\n")[1])
      .toBe("1,F,anticorrosion_coating,0.9,salt_spray_hours,500,,,0,");
  });
});

describe("versions", () => {
  const vs = [ver("a", 1, null), ver("b", 2, "a"), ver("c", 3, "b"), ver("d", 4, "b"), ver("e", 5, null)];
  it("builds a tree; orphans start new roots", () => {
    const roots = versionTree(vs);
    expect(roots.map((r) => r.name)).toEqual(["v1", "v5"]);
    expect(roots[0].children[0].children.map((c) => c.name)).toEqual(["v3", "v4"]);
  });
  it("finds the predecessor", () => {
    expect(previousVersion(vs, vs[3])?.id).toBe("b");
    expect(previousVersion(vs, vs[4])?.id).toBe("d");
    expect(previousVersion(vs, vs[0])).toBeUndefined();
  });
});

describe("misc", () => {
  it("shifts indices after removal", () => {
    expect(shiftAfterRemove(3, 1)).toBe(2);
    expect(shiftAfterRemove(1, 1)).toBeNull();
    expect(shiftAfterRemove(0, 1)).toBe(0);
  });
  it("reads Datalab sync outcome", () => {
    expect(datalabSyncOutcome({ synced: 0, errors: ["x"] }).skipped).toBe(true);
    expect(datalabSyncOutcome({ synced: 2 })).toEqual({ skipped: false, synced: 2, errors: 0 });
  });
  it("summarises DeepDiff", () => {
    expect(deepDiffSummary({ values_changed: { "root['a']": {} }, type_changes: ["root['b']"] })).toEqual(["values_changed: root['a']", "type_changes: root['b']"]);
    expect(deepDiffSummary({})).toEqual([]);
  });
  it("computes the Pareto front (min x, max y)", () => {
    expect(paretoFront([{ x: 1, y: 1 }, { x: 2, y: 3 }, { x: 3, y: 2 }, { x: 1, y: 1 }], "min", "max")).toEqual([0, 1, 3]);
  });
});
