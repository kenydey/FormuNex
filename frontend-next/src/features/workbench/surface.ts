/** Response-surface helpers (ported from legacy ``hooks/useContourGrid.ts``): IDW interpolation onto a grid. */
import type { WorkbenchRow } from "./types";

export interface SurfacePoint { x: number; y: number; z: number; id?: string | number }

/** Inverse-distance-weighted interpolation; ``grid[yi][xi]``. */
export function idwInterpolate(points: SurfacePoint[], gridX: number[], gridY: number[], power = 2): number[][] {
  return gridY.map((gy) => gridX.map((gx) => {
    let wSum = 0;
    let zSum = 0;
    for (const p of points) {
      const d2 = (p.x - gx) ** 2 + (p.y - gy) ** 2;
      if (d2 === 0) return p.z;
      const w = 1 / Math.pow(d2, power / 2);
      wSum += w;
      zSum += w * p.z;
    }
    return wSum > 0 ? zSum / wSum : 0;
  }));
}

export interface Surface {
  gridX: number[];
  gridY: number[];
  /** ECharts heatmap cells ``[xi, yi, z]``. */
  cells: [number, number, number][];
  zMin: number;
  zMax: number;
  dx: number;
  dy: number;
}

/** Interpolated surface over the points' bounding box (needs ≥ 3 points). */
export function buildSurface(points: SurfacePoint[], size = 30): Surface | null {
  if (points.length < 3) return null;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const zs = points.map((p) => p.z);
  let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  if (x0 === x1) { x0 -= 0.5; x1 += 0.5; }
  if (y0 === y1) { y0 -= 0.5; y1 += 0.5; }
  const dx = (x1 - x0) / (size - 1);
  const dy = (y1 - y0) / (size - 1);
  const gridX = Array.from({ length: size }, (_, i) => x0 + i * dx);
  const gridY = Array.from({ length: size }, (_, i) => y0 + i * dy);
  const grid = idwInterpolate(points, gridX, gridY);
  const cells: [number, number, number][] = [];
  grid.forEach((row, yi) => row.forEach((z, xi) => cells.push([xi, yi, z])));
  return { gridX, gridY, cells, zMin: Math.min(...zs), zMax: Math.max(...zs), dx, dy };
}

/**
 * Points for the surface from completed ledger rows: actual params (fallback planned) on x / y, measured metric
 * (fallback predicted) on z. Non-finite values are dropped.
 */
export function surfacePoints(rows: WorkbenchRow[], xKey: string, yKey: string, metric: string): SurfacePoint[] {
  const num = (v: unknown) => (v == null || v === "" ? NaN : Number(v));
  return rows
    .filter((r) => r.status === "Completed")
    .map((r) => ({
      id: r.id,
      x: num(r.actual_params?.[xKey] ?? r.planned_params?.[xKey]),
      y: num(r.actual_params?.[yKey] ?? r.planned_params?.[yKey]),
      z: num(r.measurements?.[metric] ?? r.predicted?.[metric]),
    }))
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z));
}
