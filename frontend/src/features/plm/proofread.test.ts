import { describe, expect, it } from "vitest";

import { pairSections, proofreadParity } from "./proofread";
import type { DocContent } from "./types";

function doc(lang: string, sections: DocContent["sections"]): DocContent {
  return { doc_type: "sds", lang, title: "T", header: {}, sections,
           validation: { missing: [], warnings: [] }, meta: {} };
}
function sec(key: string, no: number, fields: { key: string; label: string; value: unknown }[]) {
  return { no, key, title: key, fields: fields.map((f) => ({ ...f, source: "manual" })),
           tables: [], paragraphs: [] };
}

describe("proofread pairing (3-16)", () => {
  it("pairs by key, not by index", () => {
    const zh = doc("zh-CN", [sec("a", 1, [{ key: "x", label: "X", value: 1 }]),
                             sec("b", 2, [{ key: "y", label: "Y", value: 2 }])]);
    // 英文侧章节顺序不同：按索引配对会把 b 错配成 a。
    const en = doc("en-US", [sec("b", 2, [{ key: "y", label: "Y", value: 2 }]),
                             sec("a", 1, [{ key: "x", label: "X", value: 1 }])]);
    const paired = pairSections(zh, en);
    expect(paired.map((p) => p.key)).toEqual(["a", "b"]);
    expect(paired[0].fields[0].valueZh).toBe(1);
    expect(paired[0].fields[0].valueEn).toBe(1);
    expect(paired[0].missingSide).toBeNull();
  });

  it("flags missing sections and fields on either side", () => {
    const zh = doc("zh-CN", [sec("a", 1, [{ key: "x", label: "X", value: 1 },
                                         { key: "only_zh", label: "仅中文", value: 9 }])]);
    const en = doc("en-US", [sec("a", 1, [{ key: "x", label: "X", value: 1 }]),
                             sec("only_en", 2, [])]);
    const paired = pairSections(zh, en);
    expect(paired.find((p) => p.key === "only_en")?.missingSide).toBe("zh");
    expect(paired.find((p) => p.key === "a")?.fields.find((f) => f.key === "only_zh")?.missingSide).toBe("en");
    const parity = proofreadParity(zh, en);
    expect(parity.missingSections).toEqual(["only_en"]);
    expect(parity.missingFields).toEqual(["a.only_zh"]);
    expect(parity.sectionsZh).toBe(1);
    expect(parity.sectionsEn).toBe(2);
  });

  it("flags table / paragraph shape mismatch", () => {
    const zh = doc("zh-CN", [{ no: 1, key: "a", title: "a", fields: [],
                               tables: [{ key: "t", columns: ["A", "B"], rows: [[1, 2]], source: "manual" }],
                               paragraphs: ["p1", "p2"] }]);
    const en = doc("en-US", [{ no: 1, key: "a", title: "a", fields: [],
                               tables: [{ key: "t", columns: ["A"], rows: [[1]], source: "manual" }],
                               paragraphs: ["p1"] }]);
    const [p] = pairSections(zh, en);
    expect(p.tablesMismatch).toBe(true);
    expect(p.paragraphsMismatch).toBe(true);
  });
});
