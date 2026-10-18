import { describe, expect, it } from "vitest";
import { groupChunksByPage, type Chunk } from "./types";

const c = (id: string, page: number | null): Chunk =>
  ({ id, source_id: "s", ord: 0, text: id, heading_path: "", page });

describe("groupChunksByPage", () => {
  it("groups by page, ascending, unpaged last", () => {
    const out = groupChunksByPage([c("a", 3), c("b", 1), c("c", null), c("d", 1)]);
    expect(out.map((g) => g.page)).toEqual([1, 3, null]);
    expect(out[0].chunks.map((x) => x.id)).toEqual(["b", "d"]);
    expect(out[2].chunks.map((x) => x.id)).toEqual(["c"]);
  });

  it("handles empty input", () => {
    expect(groupChunksByPage([])).toEqual([]);
  });
});
