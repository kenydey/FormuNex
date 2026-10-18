import { describe, expect, it } from "vitest";
import { clamp, defaultLayout, missingRequired, SIZES } from "./labelLayout";

describe("label layout", () => {
  it("default layouts fit every GB 15258 size and contain every required element", () => {
    for (const size of SIZES) {
      const l = defaultLayout(size);
      expect(missingRequired(l)).toEqual([]);
      for (const e of l.elements) {
        expect(e.x + e.w).toBeLessThanOrEqual(size[0]);
        expect(e.y + e.h).toBeLessThanOrEqual(size[1]);
      }
    }
  });

  it("clamps to the label and snaps to the 1 mm grid", () => {
    const e = clamp({ id: "a", type: "text", x: 95.4, y: -3, w: 20.6, h: 4.2 }, [100, 150]);
    expect(e).toMatchObject({ x: 79, y: 0, w: 21, h: 4 });
  });
});
