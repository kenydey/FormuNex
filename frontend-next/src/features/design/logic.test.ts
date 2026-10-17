import { describe, expect, it } from "vitest";
import {
  deltaDisplay, designsForEngine, distinctCompositions, factorHintsToLevers, frontier, leverProblems, leverToRow, notFoundCandidates,
  parseLevels, parseMaterialMap, planActions, rowToLever, similarityFactors, specRange, strategyForPlan, supplyBadges, surechemblRef,
} from "./logic";
import type { DesignCandidate } from "./types";

describe("levers", () => {
  it("parses levels and material maps", () => {
    expect(parseLevels("聚酰胺, 酚醛，3 , 2.5,")).toEqual(["聚酰胺", "酚醛", 3, 2.5]);
    expect(parseMaterialMap("树脂B=聚氨酯树脂X, bad, =x, C = Y")).toEqual({ 树脂B: "聚氨酯树脂X", C: "Y" });
    expect(parseMaterialMap(" ")).toBeNull();
  });

  it("round-trips discrete and continuous levers", () => {
    const d = { name: "树脂", low: 0, high: 1, unit: "wt%", kind: "discrete", levels: ["A", "B"], material_map: { B: "PU" } };
    expect(rowToLever(leverToRow(d))).toEqual(d);
    const c = { name: "T", low: 120, high: 180, unit: "°C" };
    expect(rowToLever(leverToRow(c))).toEqual(c);
    expect(rowToLever({ ...leverToRow(c), kind: "discrete", levels: "x" }).levels).toEqual(["A", "B"]);
  });

  it("reports invalid levers", () => {
    const rows = [leverToRow({ name: "a", low: 5, high: 1 }), leverToRow({ name: "a", low: 0, high: 1 }),
      { ...leverToRow({ name: "", low: 0, high: 1 }), kind: "discrete" as const, levels: "one" }];
    expect(leverProblems(rows)).toEqual(["#1: low > high", "#2: duplicate a", "#3: name", "#3: levels < 2"]);
  });

  it("maps AI factor hints", () => {
    const base = { rationale: "", evidence_ids: [], source: "kb", unit: "" };
    expect(factorHintsToLevers([{ ...base, name: "x", low: 1, high: 2 }, { ...base, name: "y", low: 0, high: 0, kind: "discrete", levels: [1, 2] }]))
      .toEqual([{ name: "x", low: 1, high: 2, unit: "wt%" }, { name: "y", low: 0, high: 0, unit: "wt%", kind: "discrete", levels: [1, 2] }]);
  });
});

describe("doe", () => {
  it("lists designs per engine", () => {
    expect(designsForEngine("pydoe")).not.toContain("full_factorial");
    expect(designsForEngine("native")).not.toContain("bbdesign");
    expect(designsForEngine("auto")).toEqual(expect.arrayContaining(["full_factorial", "bbdesign", "ai_active"]));
  });
  it("follows the plan state machine", () => {
    expect(planActions(undefined)).toEqual(["activate", "abort"]);
    expect(planActions("active")).toEqual(["complete", "abort"]);
    expect(planActions("completed")).toEqual([]);
    expect(strategyForPlan("ccd")).toBe("DOE-ccd");
    expect(strategyForPlan("ai_active", "baybe")).toBe("BayBE-baybe-next");
  });
});

describe("design helpers", () => {
  const cand = (rank: number | null, materials: string[]): DesignCandidate =>
    ({ pareto_rank: rank, materials, feasible: true, violation: 0, formulation: { name: "", domain: "", ingredients: [] } });
  it("finds the front and distinct compositions", () => {
    const c = [cand(0, ["a", "b"]), cand(1, ["b", "a"]), cand(0, ["c"])];
    expect(frontier(c)).toHaveLength(2);
    expect(frontier([cand(1, []), cand(2, [])])).toHaveLength(2);
    expect(distinctCompositions(c)).toBe(2);
  });
  it("formats deltas, badges, refs and spec ranges", () => {
    expect(deltaDisplay(null).tone).toBe("none");
    expect(deltaDisplay(0.01).tone).toBe("flat");
    expect(deltaDisplay(3.21)).toEqual({ text: "+3.2%", tone: "up" });
    expect(deltaDisplay(-1)).toEqual({ text: "-1.0%", tone: "down" });
    expect(supplyBadges({ stale_price: true, long_lead_time: true })).toEqual(["stale_price", "long_lead_time"]);
    expect(supplyBadges({ supply_badges: ["x"], stale_price: true })).toEqual(["x"]);
    expect(surechemblRef({ name: "n", chemical_id: "1", similarity: 1, source: "s", in_catalog: false,
      patents: [{ doc_id: "A" }, { doc_id: "" }, { doc_id: "B" }] })).toBe("SCHEMBL:1 patents:A,B");
    expect(specRange({ spec_min: 1, spec_max: 2 })).toBe("1 ~ 2");
    expect(specRange({ spec_min: null, spec_max: 2 })).toBe("≤ 2");
    expect(specRange({ spec_min: null, spec_max: null })).toBe("—");
    expect(notFoundCandidates({ message: "m", candidates: ["a", 3, ""] })).toEqual(["a"]);
    expect(notFoundCandidates("x")).toEqual([]);
  });
  it("builds similarity factors from ingredients unless explicit factors exist", () => {
    expect(similarityFactors({ ingredients: [{ name: "a", role: "", weight_pct: 1 }, { name: "a", role: "", weight_pct: 2 },
      { name: "b", role: "", weight_pct: 0 }] })).toEqual({ a: 3 });
    expect(similarityFactors({ factors: { x: 5 }, ingredients: [{ name: "a", role: "", weight_pct: 1 }] })).toEqual({ x: 5 });
  });
});
