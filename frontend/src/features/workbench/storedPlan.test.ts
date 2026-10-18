import { describe, expect, it, vi } from "vitest";

const call = vi.fn(async (_m: string, _p: string) => ({ plan_id: "abc", design: "lhs" }));
vi.mock("@/api/client", () => ({ call: (m: string, p: string) => call(m, p) }));

import { fetchStoredPlan, storedPlanLabel, toDoePlan } from "./storedPlan";

describe("stored DOE plans", () => {
  it("reads a plan by id, scoped to the project", async () => {
    await fetchStoredPlan("a/b", "p1");
    expect(call).toHaveBeenLastCalledWith("GET", "/api/doe/plans/a%2Fb?project_id=p1");
    await fetchStoredPlan("abc");
    expect(call).toHaveBeenLastCalledWith("GET", "/api/doe/plans/abc");
  });

  it("maps a stored plan to the campaign-creation body", () => {
    const p = toDoePlan({ plan_id: "abc12345xyz", design: "ccd", domain: null, runs: [{ run_id: 1, natural: { A: 1 } }],
      campaign_id: 3, project_id: "p1" });
    expect(p).toEqual({ design: "ccd", factors: [], runs: [{ run_id: 1, natural: { A: 1 } }], notes: "", plan_id: "abc12345xyz", status: "draft" });
    expect(storedPlanLabel({ plan_id: "abc12345xyz", design: "ccd", runs: [], round: 2, created_at: "2026-10-01T10:20:30" }))
      .toBe("ccd · n=0 · R2 · 2026-10-01 10:20 · abc12345");
  });
});
