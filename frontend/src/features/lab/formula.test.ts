import { describe, expect, it } from "vitest";
import { applyAnnotations, extractAnnotations, fillWith, groupSubtotals, normalize, redLineViolations, suggestRole, total, totalCost, totalVoc, type Row } from "./formula";

const rows = (): Row[] => [
  { uid: "a", name: "环氧树脂", weight_pct: 40, cas_no: "25068-38-6" },
  { uid: "b", name: "二甲苯", weight_pct: 30, cas_no: "1330-20-7" },
  { uid: "c", name: "钛白粉", weight_pct: 10, locked: true },
];

describe("formulation editor logic", () => {
  it("totals and normalises unlocked rows only", () => {
    expect(total(rows())).toBe(80);
    const n = normalize(rows());
    expect(total(n)).toBe(100);
    expect(n.find((r) => r.uid === "c")!.weight_pct).toBe(10);
    expect(n.find((r) => r.uid === "a")!.weight_pct).toBeCloseTo(51.4286, 3);
  });

  it("fills the balance with one component", () => {
    const f = fillWith(rows(), "b");
    expect(f.find((r) => r.uid === "b")!.weight_pct).toBe(50);
    expect(total(f)).toBe(100);
    expect(() => fillWith([{ uid: "x", name: "", weight_pct: 120 }, { uid: "y", name: "", weight_pct: 0 }], "y")).toThrow();
  });

  it("flags red-line substances, pictograms and signal words", () => {
    const v = redLineViolations(rows(), {
      red_lines: [{ cas: "1330-20-7", max_pct: 25, name: "二甲苯", reason: "客户禁用" }],
      forbidden_pictograms: ["GHS08"], forbidden_signal_word: "危险",
    }, { pictograms: [{ code: "GHS02" }, { code: "GHS08" }], signal_word: "危险" });
    expect(v.map((x) => x.uid).filter(Boolean)).toEqual(["b"]);
    expect(v.some((x) => x.message.includes("GHS08"))).toBe(true);
    expect(v.some((x) => x.message.includes("信号词"))).toBe(true);
    expect(redLineViolations(rows(), { red_lines: [{ cas: "1330-20-7", max_pct: 35 }] })).toEqual([]);
  });

  it("suggests roles from name / Chinese name / CAS", () => {
    expect(suggestRole({ name: "Epoxy resin", weight_pct: 0 } as Row)).toBe("resin");
    expect(suggestRole({ name: "", zh_name: "二甲苯", weight_pct: 0 } as Row)).toBe("solvent");
    expect(suggestRole({ name: "", zh_name: "钛白粉", weight_pct: 0 } as Row)).toBe("pigment");
    expect(suggestRole({ name: "", zh_name: "去离子水", cas_no: "7732-18-5", weight_pct: 0 } as Row)).toBe("water");
    expect(suggestRole({ name: "mystery stuff", weight_pct: 0 } as Row)).toBe("other");
    // "水性环氧树脂" contains 水 but is a resin: specific keywords win over the generic water rule.
    expect(suggestRole({ name: "", zh_name: "水性环氧树脂", weight_pct: 0 } as Row)).toBe("resin");
  });

  it("computes cost / VOC totals and per-group subtotals", () => {
    const rs: Row[] = [
      { uid: "a", name: "树脂", weight_pct: 50, cost_per_kg: 40, voc_g_l: 0, group: "A" },
      { uid: "b", name: "二甲苯", weight_pct: 30, cost_per_kg: 10, voc_g_l: 870, group: "A" },
      { uid: "c", name: "钛白", weight_pct: 20, cost_per_kg: 20, voc_g_l: 0 },
    ];
    expect(totalCost(rs)).toBe(20 + 3 + 4);
    expect(totalVoc(rs)).toBe(261);
    const groups = groupSubtotals(rs);
    expect(groups.find((g) => g.group === "A")).toMatchObject({ weight_pct: 80, cost: 23, voc: 261, count: 2 });
    expect(groups.find((g) => g.group === "")).toMatchObject({ weight_pct: 20, count: 1 });
  });

  it("round-trips editor annotations by ingredient identity", () => {
    const rs: Row[] = [{ uid: "a", name: "树脂", cas_no: "25068-38-6", weight_pct: 50, cost_per_kg: 40, group: "A" }];
    const anns = extractAnnotations(rs);
    expect(anns).toEqual({ "树脂||25068-38-6": { cost_per_kg: 40, group: "A" } });
    // Reloaded rows get fresh uids; annotations re-attach by name + CAS.
    const reloaded = applyAnnotations([{ uid: "fresh", name: "树脂", cas_no: "25068-38-6", weight_pct: 50 }], anns);
    expect(reloaded[0]).toMatchObject({ cost_per_kg: 40, group: "A" });
  });
});
