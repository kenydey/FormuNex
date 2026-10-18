import { describe, expect, it } from "vitest";
import { extractStormToc, stormSlug } from "./stormToc";

describe("stormToc", () => {
  it("slugs titles keeping CJK and collapsing spaces", () => {
    expect(stormSlug("技术背景与目标指标")).toBe("技术背景与目标指标");
    expect(stormSlug("DOE 与实验台账解读")).toBe("doe-与实验台账解读");
    expect(stormSlug("  Hello, World!  ")).toBe("hello-world");
    expect(stormSlug("")).toBe("section");
  });

  it("extracts headings with levels and skips fenced code", () => {
    const md = [
      "# 报告标题",
      "```",
      "# 不是标题",
      "```",
      "## 技术背景与目标指标",
      "### **加粗**小节",
      "## [链接标题](https://example.com)",
    ].join("\n");
    expect(extractStormToc(md)).toEqual([
      { level: 1, title: "报告标题", slug: "报告标题" },
      { level: 2, title: "技术背景与目标指标", slug: "技术背景与目标指标" },
      { level: 3, title: "加粗小节", slug: "加粗小节" },
      { level: 2, title: "链接标题", slug: "链接标题" },
    ]);
  });

  it("dedupes repeated titles like GitHub", () => {
    const md = "## 风险\n## 风险\n## 风险";
    expect(extractStormToc(md).map((e) => e.slug)).toEqual(["风险", "风险-1", "风险-2"]);
  });

  it("returns empty for no headings", () => {
    expect(extractStormToc("just text\nno headings")).toEqual([]);
    expect(extractStormToc("")).toEqual([]);
  });
});
