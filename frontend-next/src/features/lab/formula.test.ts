import { describe, expect, it } from "vitest";
import { fillWith, normalize, redLineViolations, total, type Row } from "./formula";

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
});
