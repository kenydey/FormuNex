import { describe, expect, it } from "vitest";
import { keepFailedSelection } from "./ReviewQueuePage";

describe("bulk review selection (SR-074)", () => {
  it("keeps only the failed rows selected", () => {
    expect(keepFailedSelection(["a", "b", "c"], new Set(["b"]))).toEqual(["b"]);
  });

  it("clears the selection when everything succeeded", () => {
    expect(keepFailedSelection(["a", "b"], new Set())).toEqual([]);
  });

  it("keeps order and ignores failed ids outside the selection", () => {
    expect(keepFailedSelection(["b", "a"], new Set(["a", "z"]))).toEqual(["a"]);
  });
});
