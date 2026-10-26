import { describe, expect, it } from "vitest";
import { fromEditorRows, toEditorRows, validateCustomWindow } from "./customWindow";

describe("validateCustomWindow", () => {
  it("accepts range and discrete rows", () => {
    expect(validateCustomWindow([
      { key: "bake_humidity", label: "烘烤湿度", kind: "range", unit: "%", low: 30, high: 60 },
      { key: "pretreat", label: "前处理", kind: "discrete", values: ["喷砂", "抛丸"] },
    ])).toBeNull();
  });

  it("rejects empty keys, bad key format, fixed-key conflicts and duplicates", () => {
    expect(validateCustomWindow([{ key: "", label: "x", kind: "range", low: 0, high: 1 }])).toMatch(/英文键不能为空/);
    expect(validateCustomWindow([{ key: "Bake Temp", label: "x", kind: "range", low: 0, high: 1 }])).toMatch(/格式非法/);
    expect(validateCustomWindow([{ key: "cure_temp_c", label: "x", kind: "range", low: 0, high: 1 }])).toMatch(/固定项冲突/);
    expect(validateCustomWindow([
      { key: "a", label: "x", kind: "range", low: 0, high: 1 },
      { key: "a", label: "y", kind: "range", low: 0, high: 1 },
    ])).toMatch(/重复/);
  });

  it("rejects inverted ranges, missing bounds and short discrete lists", () => {
    expect(validateCustomWindow([{ key: "a", label: "x", kind: "range", low: 5, high: 5 }])).toMatch(/下限必须小于上限/);
    expect(validateCustomWindow([{ key: "a", label: "x", kind: "range", low: null, high: 5 }])).toMatch(/需要上下限/);
    expect(validateCustomWindow([{ key: "a", label: "x", kind: "discrete", values: ["only"] }])).toMatch(/至少需要 2 个取值/);
    expect(validateCustomWindow([{ key: "a", label: "", kind: "range", low: 0, high: 1 }])).toMatch(/中文名不能为空/);
  });
});

describe("editor row round-trip", () => {
  it("discrete values go through comma text and dedupe", () => {
    const rows = fromEditorRows(toEditorRows([{ key: "pretreat", label: "前处理", kind: "discrete", values: ["喷砂", "抛丸"] }]));
    expect(rows).toEqual([{ key: "pretreat", label: "前处理", kind: "discrete", values: ["喷砂", "抛丸"], low: undefined, high: undefined }]);
    const edited = fromEditorRows([{ key: "pretreat", label: "前处理", kind: "discrete", values_text: "喷砂， 抛丸、喷砂" }]);
    expect(edited[0].values).toEqual(["喷砂", "抛丸"]);
  });

  it("range rows drop values and values_text", () => {
    const rows = fromEditorRows([{ key: "h", label: "湿度", kind: "range", unit: "%", low: 30, high: 60, values_text: "a, b" }]);
    expect(rows[0]).not.toHaveProperty("values");
    expect(rows[0]).not.toHaveProperty("values_text");
  });
});
