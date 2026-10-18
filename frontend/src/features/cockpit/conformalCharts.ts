/** 补齐 2-18: 偏差校正可视化纯函数（预测–实测散点 + 残差直方图分箱）。 */

export interface Pair {
  predicted: number;
  actual: number;
}

/** 散点数据：x=预测，y=实测。 */
export function scatterPoints(pairs: Pair[]): { x: number; y: number }[] {
  return pairs.map((p) => ({ x: p.predicted, y: p.actual }));
}

/** y=x 对角线（完美预测参考线），取预测/实测联合范围。 */
export function diagonalLine(pairs: Pair[]): [[number, number], [number, number]] {
  if (!pairs.length) return [[0, 0], [1, 1]];
  const vals = pairs.flatMap((p) => [p.predicted, p.actual]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  return [[lo, lo], [hi, hi]];
}

export interface HistBin {
  start: number;
  end: number;
  count: number;
}

/** 残差（预测−实测）等宽直方图分箱；空输入返回空数组。 */
export function residualHistogram(pairs: Pair[], bins = 20): HistBin[] {
  const residuals = pairs.map((p) => p.predicted - p.actual);
  if (!residuals.length || bins < 1) return [];
  const lo = Math.min(...residuals);
  const hi = Math.max(...residuals);
  if (lo === hi) return [{ start: lo, end: hi, count: residuals.length }];
  const width = (hi - lo) / bins;
  const out: HistBin[] = Array.from({ length: bins }, (_, i) => ({
    start: lo + i * width,
    end: lo + (i + 1) * width,
    count: 0,
  }));
  for (const r of residuals) {
    const idx = Math.min(bins - 1, Math.floor((r - lo) / width));
    out[idx].count += 1;
  }
  return out;
}

/** 残差均值（偏差）与均方根误差。 */
export function residualStats(pairs: Pair[]): { bias: number; rmse: number } {
  if (!pairs.length) return { bias: 0, rmse: 0 };
  const residuals = pairs.map((p) => p.predicted - p.actual);
  const bias = residuals.reduce((a, b) => a + b, 0) / residuals.length;
  const rmse = Math.sqrt(residuals.reduce((a, b) => a + b * b, 0) / residuals.length);
  return { bias, rmse };
}
