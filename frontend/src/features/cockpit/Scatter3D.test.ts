import { describe, expect, it } from "vitest";
import { normalize3D, projectPoint } from "./Scatter3D";

describe("Scatter3D", () => {
  it("零旋转时投影为 x/y 直取、深度为 z", () => {
    const p = projectPoint([0.5, -0.5, 0.25], 0, 0, 100, 200, 150);
    expect(p.x).toBeCloseTo(250, 10);
    expect(p.y).toBeCloseTo(200, 10);
    expect(p.depth).toBeCloseTo(0.25, 10);
  });

  it("绕 Y 旋转 90° 把 x 轴转到深度方向", () => {
    const p = projectPoint([1, 0, 0], 0, Math.PI / 2, 100, 0, 0);
    expect(p.x).toBeCloseTo(0, 10);
    expect(p.depth).toBeCloseTo(-1, 10);
  });

  it("归一化把每轴映射到 [-1, 1]，恒定轴为 0", () => {
    const out = normalize3D([
      { id: "a", values: [0, 5, 1], frontier: false },
      { id: "b", values: [10, 5, 3], frontier: true },
    ]);
    expect(out[0].v).toEqual([-1, 0, -1]);
    expect(out[1].v).toEqual([1, 0, 1]);
  });
});
