import { describe, expect, it } from "vitest";
import { diagonalLine, residualHistogram, residualStats, scatterPoints } from "./conformalCharts";

describe("conformalCharts", () => {
  it("散点 x=预测 y=实测", () => {
    expect(scatterPoints([{ predicted: 1, actual: 2 }])).toEqual([{ x: 1, y: 2 }]);
  });

  it("对角线为 y=x 且覆盖联合范围", () => {
    expect(diagonalLine([{ predicted: 0, actual: 10 }, { predicted: 5, actual: 3 }])).toEqual([[0, 0], [10, 10]]);
    expect(diagonalLine([])).toEqual([[0, 0], [1, 1]]);
  });

  it("残差直方图计数之和等于点数", () => {
    const pairs = Array.from({ length: 50 }, (_, i) => ({ predicted: i, actual: i * 0.9 }));
    const bins = residualHistogram(pairs, 10);
    expect(bins).toHaveLength(10);
    expect(bins.reduce((a, b) => a + b.count, 0)).toBe(50);
    expect(bins[0].start).toBeLessThanOrEqual(bins[0].end);
  });

  it("恒定残差落入单个箱", () => {
    expect(residualHistogram([{ predicted: 5, actual: 3 }, { predicted: 6, actual: 4 }], 10))
      .toEqual([{ start: 2, end: 2, count: 2 }]);
  });

  it("残差统计：bias 与 rmse", () => {
    const { bias, rmse } = residualStats([{ predicted: 3, actual: 1 }, { predicted: 1, actual: 1 }]);
    expect(bias).toBeCloseTo(1, 10);
    expect(rmse).toBeCloseTo(Math.SQRT2, 10);
  });
});
