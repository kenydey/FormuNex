import { describe, expect, it } from "vitest";
import {
  applyPaste, buildSyncRows, editableColumns, effectiveObjectives, factorKeys, ledgerStats, loopSummary, loopTone, parseClipboard,
  parseNumeric, rowChanges, specText, toCsv, trainingRecords, valueMeetsSpec,
} from "./ledger";
import { loopBody, workspaceAfterLoop } from "./loop";
import { measuredHits } from "./testFixtures";
import { summarizeHits } from "./measuredHits";
import type { LoopReport, ObjectiveSpec, WorkbenchRow } from "./types";

const row = (id: number, p: Partial<WorkbenchRow> = {}): WorkbenchRow => ({
  id, campaign_id: 1, status: "Pending", planned_params: { A: 10, B: 20 }, actual_params: {}, measurements: {}, ...p,
});
const salt: ObjectiveSpec = { metric: "salt", direction: "maximize", ref_min: 500, unit: "h" };
const grade: ObjectiveSpec = { metric: "adhesion_grade", direction: "maximize" };

describe("ledger helpers", () => {
  it("parses Excel numbers", () => {
    expect(parseNumeric(" 1,234.5 ")).toBe(1234.5);
    expect(parseNumeric("")).toBeNull();
    expect(parseNumeric("abc")).toBeNull();
    expect(parseNumeric(3)).toBe(3);
  });

  it("formats and checks specs", () => {
    expect(specText(salt)).toBe("≥ 500 h");
    expect(specText({ metric: "x", direction: "minimize", ref_min: 1, ref_max: 2 })).toBe("1–2");
    expect(valueMeetsSpec(salt, 600)).toBe(true);
    expect(valueMeetsSpec(salt, 100)).toBe(false);
    expect(valueMeetsSpec(salt, "")).toBeNull();
    expect(valueMeetsSpec({ metric: "y", direction: "match_target", target_value: 10 }, 10.5)).toBe(true);
  });

  it("derives factors and objectives", () => {
    expect(factorKeys(null, [row(1)])).toEqual(["A", "B"]);
    expect(factorKeys({ factors: [{ name: "Z", low: 0, high: 1 }] }, [row(1)])).toEqual(["Z"]);
    expect(effectiveObjectives([salt], [grade])).toEqual([grade]);
    expect(effectiveObjectives([], null)[0].metric).toBe("salt_spray_hours");
  });

  it("builds sync rows with only objective metrics and finite params", () => {
    const r = row(1, { actual_params: { A: 11, B: null }, measurements: { salt: 600, other: 1, adhesion_grade: "" } });
    expect(buildSyncRows([r], ["salt", "adhesion_grade"])[0]).toMatchObject({ id: 1, actual_params: { A: 11 }, measurements: { salt: 600 } });
  });

  it("pastes a block of cells like a spreadsheet", () => {
    const rows = [row(1), row(2), row(3)];
    const cols = editableColumns(["A", "B"], [salt, grade]);
    const cells = parseClipboard("Completed\t12\t21\t650\tA\r\ncompleted\t13\t22\tbad\tB\n\n");
    const res = applyPaste(rows, cells, 1, 0, cols, [salt, grade]);
    expect(res.changed).toEqual([2, 3]);
    expect(res.rows[1]).toMatchObject({ status: "Completed", actual_params: { A: 12, B: 21 }, measurements: { salt: 650, adhesion_grade: "A" } });
    expect(res.rows[2].measurements.salt).toBeNull();
    expect(res.rows[0]).toBe(rows[0]);
  });

  it("counts, exports and diffs", () => {
    const rows = [row(1, { status: "Completed", measurements: { salt: 600 }, ingested: true, note: 'a,"b"' }), row(2)];
    expect(ledgerStats(rows)).toEqual({ total: 2, completed: 1, measured: 1, ingested: 1 });
    const csv = toCsv(rows, ["A"], [salt]);
    expect(csv.split("\n")[0]).toBe("﻿id,status,planned:A,actual:A,salt,note,tags");
    expect(csv).toContain('"a,""b"""');
    const ch = rowChanges(rows[0], rows[1]);
    expect(ch.find((c) => c.path === "measurements.salt")).toMatchObject({ kind: "removed", major: true });
    expect(ch.find((c) => c.path === "status")).toMatchObject({ kind: "changed" });
  });

  it("summarises loop state", () => {
    expect(loopTone("idle", true)).toBe("converged");
    expect(loopTone("paused", false)).toBe("paused");
    expect(loopSummary(null, [{ converged: false }, { converged: true }])).toMatchObject({ rounds: 2, converged: true, status: "idle" });
  });

  it("builds training records from completed measured rows", () => {
    const recs = trainingRecords([row(1, { status: "Completed", actual_params: { A: 9 }, measurements: { salt: "700" } }), row(2, { measurements: { salt: 1 } })],
      ["salt"], "degreaser", "p1");
    expect(recs).toHaveLength(1);
    expect(recs[0]).toMatchObject({ domain: "degreaser", project_id: "p1", factors: { A: 9, B: 20 }, measured: { salt: 700 }, source: "workbench" });
  });
});

describe("loop helpers", () => {
  const report = {
    domain: "d", total_records: 3, model_info: [], rmse_by_metric: { salt: 4 }, engine: "baybe", converged: false, loop_message: "",
    optimization: { history: [1, 2], top_formulations: [{ name: "F" }] }, next_doe: { design: "ai_active", factors: [], runs: [] },
    campaign_state: "S", strategy_label: "balanced",
  } as unknown as LoopReport;

  it("appends RMSE history and replaces the next DOE", () => {
    const ws = workspaceAfterLoop({ rmse_history: [{ salt: 5 }] }, report);
    expect(ws.rmse_history).toEqual([{ salt: 5 }, { salt: 4 }]);
    expect(ws.doe_plan).toBe(report.next_doe);
    expect(ws.campaign_state).toBe("S");
    expect((ws.adaptive_doe as { strategy_label: string }).strategy_label).toBe("balanced");
  });

  it("keeps the previous plan when converged with nothing new", () => {
    const ws = workspaceAfterLoop({}, { ...report, converged: true, optimization: { history: [], top_formulations: [] } });
    expect(ws.doe_plan).toBeUndefined();
    expect(ws.leaderboard).toBeUndefined();
  });

  it("sends prior state with the loop request", () => {
    const b = loopBody({ domain: "d" }, { loop_doe_engine: "baybe", rmse_history: [{ a: 1 }], loop_report: report }, 7);
    expect(b).toMatchObject({ domain: "d", doe_engine: "baybe", optimize_engine: "auto", workbench_campaign_id: 7, prior_next_doe: report.next_doe });
  });
});

describe("measured hits", () => {
  it("picks the strongest quality", () => {
    expect(summarizeHits(measuredHits)).toEqual({ quality: "good", materials: ["X", "Y"], metrics: ["salt"] });
    expect(summarizeHits([])).toBeNull();
  });
});
