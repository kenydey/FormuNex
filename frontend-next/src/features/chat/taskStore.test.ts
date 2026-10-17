import { describe, expect, it } from "vitest";
import { mergeServerTasks, type ServerTask, type TrackedTask } from "./taskStore";

const srv = (id: string, state: ServerTask["state"], at: string, extra: Partial<ServerTask> = {}): ServerTask => ({
  task_id: id, kind: "recommend", state, progress: state === "completed" ? 1 : 0.2, message: "m", stage: "", stream_url: "", updated_at: at, ...extra,
});
const loc = (id: string, state: TrackedTask["state"], startedAt: number, extra: Partial<TrackedTask> = {}): TrackedTask => ({
  id, state, progress: 0, message: "", steps: [], startedAt, ...extra,
});

describe("mergeServerTasks", () => {
  it("adds server-only tasks, newest first, flagged as server", () => {
    const out = mergeServerTasks([loc("a", "running", Date.parse("2026-01-02T00:00:00Z"))],
      [srv("b", "completed", "2026-01-03T00:00:00Z"), srv("c", "failed", "2026-01-01T00:00:00Z")]);
    expect(out.map((t) => t.id)).toEqual(["b", "a", "c"]);
    expect(out[0]).toMatchObject({ server: true, kind: "recommend", state: "completed" });
    expect(out[1].server).toBeUndefined();
  });

  it("keeps local title/link but adopts a terminal server state", () => {
    const out = mergeServerTasks([loc("a", "running", 1, { title: "T", link: "/x" })], [srv("a", "completed", "2026-01-01T00:00:00Z")]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ id: "a", title: "T", link: "/x", state: "completed", progress: 1 });
  });

  it("does not regress a local terminal task and skips hidden server tasks", () => {
    const out = mergeServerTasks([loc("a", "failed", 1)], [srv("a", "running", "2026-01-01T00:00:00Z"), srv("h", "completed", "2026-01-01T00:00:00Z")], ["h"]);
    expect(out.map((t) => [t.id, t.state])).toEqual([["a", "failed"]]);
  });
});
