import { describe, expect, it } from "vitest";
import { buildSurface, idwInterpolate, surfacePoints } from "./surface";

describe("response surface", () => {
  it("interpolates exactly at data points and between them", () => {
    const g = idwInterpolate([{ x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 10 }], [0, 1, 2], [0]);
    expect(g[0][0]).toBe(0);
    expect(g[0][2]).toBe(10);
    expect(g[0][1]).toBeCloseTo(5);
  });

  it("needs three points and spans their box", () => {
    expect(buildSurface([{ x: 0, y: 0, z: 1 }])).toBeNull();
    const s = buildSurface([{ x: 0, y: 0, z: 1 }, { x: 10, y: 5, z: 3 }, { x: 5, y: 2, z: 2 }], 11)!;
    expect(s.gridX[0]).toBe(0);
    expect(s.gridX[10]).toBe(10);
    expect(s.cells).toHaveLength(121);
    expect([s.zMin, s.zMax]).toEqual([1, 3]);
  });

  it("uses actual params, falling back to planned, for completed rows only", () => {
    const pts = surfacePoints([
      { id: 1, campaign_id: 1, status: "Completed", planned_params: { a: 1, b: 2 }, actual_params: { a: 1.5 }, measurements: { m: 9 } },
      { id: 2, campaign_id: 1, status: "Pending", planned_params: { a: 1, b: 2 }, actual_params: {}, measurements: { m: 9 } },
      { id: 3, campaign_id: 1, status: "Completed", planned_params: { a: 1, b: 2 }, actual_params: {}, measurements: {} },
    ], "a", "b", "m");
    expect(pts).toEqual([{ id: 1, x: 1.5, y: 2, z: 9 }]);
  });
});
