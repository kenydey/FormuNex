import { describe, expect, it } from "vitest";
import { addCorrection, exportCorrections, findCorrection, removeCorrection } from "./ocsrCorrections";
import type { OcsrCorrection } from "./ocsrCorrections";

const one = (over: Partial<OcsrCorrection> = {}): OcsrCorrection =>
  ({ id: "1", original: "C1CC", corrected: "c1ccccc1", created_at: "2026-01-01T00:00:00Z", ...over });

describe("OCSR correction ledger", () => {
  it("upserts by original SMILES, newest first", () => {
    const list = addCorrection([one()], "C1CC", "CCO", "second");
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ original: "C1CC", corrected: "CCO", note: "second" });
    expect(addCorrection(list, "C1CC", "C1CC")).toBe(list); // no-op: identical
    expect(addCorrection(list, "  ", "CC")).toBe(list); // no-op: empty
  });

  it("finds and removes corrections", () => {
    const list = [one(), one({ id: "2", original: "X", corrected: "Y" })];
    expect(findCorrection(list, " X ")).toMatchObject({ corrected: "Y" });
    expect(findCorrection(list, "missing")).toBeUndefined();
    expect(removeCorrection(list, "1")).toHaveLength(1);
  });

  it("exports JSON", () => {
    expect(JSON.parse(exportCorrections([one()]))).toHaveLength(1);
  });
});
