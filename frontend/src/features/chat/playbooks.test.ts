import { describe, expect, it } from "vitest";
import { BUILTIN_PLAYBOOKS, builtinTarget, playbookTarget, uncoveredBuiltins, withQuery } from "./playbooks";

describe("playbooks", () => {
  it("maps backend playbook actions to pages and carries question + project", () => {
    const t = playbookTarget({ id: "formula_recommend", title: "配方推荐", summary: "s", action: "recommend" }, { question: " 耐盐雾 ", projectId: "p1" });
    expect(t).toEqual({ kind: "navigate", to: "/lab/recommend?q=%E8%80%90%E7%9B%90%E9%9B%BE",
      state: { question: "耐盐雾", projectId: "p1", playbook: "formula_recommend" } });
    expect(playbookTarget({ id: "x", title: "", summary: "", action: "doe" }, {})).toMatchObject({ to: "/lab/doe?tab=classic" });
    expect(playbookTarget({ id: "x", title: "", summary: "", action: "optimize" }, {})).toMatchObject({ to: "/cockpit/optimization?tab=loop" });
    expect(playbookTarget({ id: "x", title: "", summary: "", action: "deep_research" }, { question: "a b" }))
      .toMatchObject({ to: "/knowledge/research?tab=deep&q=a+b" });
  });

  it("prefills the composer for playbooks without a page", () => {
    expect(playbookTarget({ id: "pack", title: "硅烷", summary: "看这个", action: "custom" }, { question: "Q" }))
      .toEqual({ kind: "prefill", text: "【硅烷】 看这个\nQ" });
    expect(playbookTarget({ id: "pack", title: "硅烷", summary: "" }, {})).toEqual({ kind: "prefill", text: "【硅烷】\n" });
  });

  it("hides built-ins already covered by backend playbooks", () => {
    const ids = uncoveredBuiltins([{ action: "recommend" }, { action: "optimize" }]).map((b) => b.id);
    expect(ids).not.toContain("recommend");
    expect(ids).not.toContain("loop");
    expect(ids).toContain("ip_analysis");
    expect(uncoveredBuiltins([])).toHaveLength(BUILTIN_PLAYBOOKS.length);
  });

  it("withQuery keeps existing params and skips empty values", () => {
    expect(withQuery("/a?tab=deep", "q", "")).toBe("/a?tab=deep");
    expect(withQuery("/a", undefined, "x")).toBe("/a");
    const ip = BUILTIN_PLAYBOOKS.find((b) => b.id === "ip_analysis")!;
    expect(builtinTarget(ip, { question: "q" })).toMatchObject({ to: "/knowledge/research/ip", state: { question: "q", playbook: "ip_analysis" } });
  });
});
