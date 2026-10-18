import { describe, expect, it } from "vitest";
import type { LabTask } from "./types";
import { siblingNav } from "./matrixNav";

const mk = (id: string, run_index: number | null): LabTask => ({
  id, task_no: id, status: "open", project_id: null, doe_plan_id: "p1", run_index,
  factors: {}, requested_metrics: [], assignee_id: null, notes: "", due_at: null,
  sample_no: null, experiment_id: null,
});

describe("siblingNav（3-17：DOE 矩阵顺序导航）", () => {
  it("中间任务返回上 / 下一运行", () => {
    const { prev, next } = siblingNav([mk("c", 3), mk("a", 1), mk("b", 2)], "b");
    expect(prev?.id).toBe("a");
    expect(next?.id).toBe("c");
  });
  it("首 / 末运行一侧为 null", () => {
    expect(siblingNav([mk("a", 1), mk("b", 2)], "a").prev).toBeNull();
    expect(siblingNav([mk("a", 1), mk("b", 2)], "b").next).toBeNull();
  });
  it("当前任务不在列表中时两侧为 null", () => {
    expect(siblingNav([mk("a", 1)], "zzz")).toEqual({ prev: null, next: null });
  });
  it("run_index 为空的任务排在最后", () => {
    const { next } = siblingNav([mk("x", null), mk("a", 1)], "a");
    expect(next?.id).toBe("x");
  });
});
