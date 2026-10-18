import { useCallback, useEffect, useRef, useState } from "react";

/** 补齐 2-19: 轻量 3D 散点（Canvas + 手动正交投影，不引 echarts-gl）。 */

export interface Scatter3DPoint {
  id: string;
  values: [number, number, number];
  frontier: boolean;
}

export interface Projected {
  x: number;
  y: number;
  /** 投影后的深度（越大越靠前），用于尺寸/透明度编码。 */
  depth: number;
}

/** 绕 Y 再绕 X 旋转后的正交投影。输入为已归一化到 [-1, 1] 的坐标。 */
export function projectPoint(
  v: [number, number, number],
  rotX: number,
  rotY: number,
  scale: number,
  cx: number,
  cy: number,
): Projected {
  const [x, y, z] = v;
  const cosY = Math.cos(rotY);
  const sinY = Math.sin(rotY);
  const x1 = x * cosY + z * sinY;
  const z1 = -x * sinY + z * cosY;
  const cosX = Math.cos(rotX);
  const sinX = Math.sin(rotX);
  const y1 = y * cosX - z1 * sinX;
  const z2 = y * sinX + z1 * cosX;
  return { x: cx + x1 * scale, y: cy - y1 * scale, depth: z2 };
}

/** 按每轴 min/max 归一化到 [-1, 1]；恒定轴映射为 0。 */
export function normalize3D(points: Scatter3DPoint[]): { id: string; v: [number, number, number]; frontier: boolean }[] {
  const mins = [Infinity, Infinity, Infinity];
  const maxs = [-Infinity, -Infinity, -Infinity];
  for (const p of points) {
    for (let i = 0; i < 3; i++) {
      mins[i] = Math.min(mins[i], p.values[i]);
      maxs[i] = Math.max(maxs[i], p.values[i]);
    }
  }
  return points.map((p) => ({
    id: p.id,
    frontier: p.frontier,
    v: [
      maxs[0] > mins[0] ? ((p.values[0] - mins[0]) / (maxs[0] - mins[0])) * 2 - 1 : 0,
      maxs[1] > mins[1] ? ((p.values[1] - mins[1]) / (maxs[1] - mins[1])) * 2 - 1 : 0,
      maxs[2] > mins[2] ? ((p.values[2] - mins[2]) / (maxs[2] - mins[2])) * 2 - 1 : 0,
    ] as [number, number, number],
  }));
}

export function Scatter3D({ points, axisNames, selectedId, onSelect, height = 420 }: {
  points: Scatter3DPoint[];
  axisNames: [string, string, string];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [rot, setRot] = useState({ x: 0.5, y: -0.7 });
  const dragRef = useRef<{ x: number; y: number; rotX: number; rotY: number } | null>(null);
  const projectedRef = useRef<{ id: string; x: number; y: number; depth: number; frontier: boolean }[]>([]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2;
    const cy = h / 2;
    const scale = Math.min(w, h) * 0.36;
    const norm = normalize3D(points);
    // 坐标轴：原点到三轴端点。
    const axes: [number, number, number][] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    ctx.strokeStyle = "#8c8c8c";
    ctx.fillStyle = "#595959";
    ctx.font = "12px sans-serif";
    for (let i = 0; i < 3; i++) {
      const a = projectPoint(axes[i] as [number, number, number], rot.x, rot.y, scale, cx, cy);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(a.x, a.y);
      ctx.stroke();
      ctx.fillText(axisNames[i], a.x + 6, a.y + 4);
    }
    // 远→近绘制，深度编码尺寸与透明度。
    const projected = norm
      .map((p) => ({ ...projectPoint(p.v, rot.x, rot.y, scale, cx, cy), id: p.id, frontier: p.frontier }))
      .sort((a, b) => a.depth - b.depth);
    projectedRef.current = projected;
    for (const p of projected) {
      const t = (p.depth + 1) / 2; // 0 远 → 1 近
      const r = (p.frontier ? 7 : 5) * (0.6 + 0.4 * t);
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = p.frontier ? `rgba(82, 196, 26, ${0.45 + 0.55 * t})` : `rgba(24, 144, 255, ${0.3 + 0.5 * t})`;
      ctx.fill();
      if (p.id === selectedId) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = "#fa8c16";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }
  }, [points, axisNames, rot, selectedId]);

  useEffect(() => { draw(); }, [draw]);

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    dragRef.current = { x: e.clientX, y: e.clientY, rotX: rot.x, rotY: rot.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (!d) return;
    setRot({ x: d.rotX + (e.clientY - d.y) * 0.01, y: d.rotY + (e.clientX - d.x) * 0.01 });
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    // 拖拽距离小才算点击：选最近的投影点。
    if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    let best: { id: string; dist: number } | null = null;
    for (const p of projectedRef.current) {
      const dist = Math.hypot(p.x - px, p.y - py);
      if (dist < 14 && (!best || dist < best.dist)) best = { id: p.id, dist };
    }
    if (best) onSelect?.(best.id);
  };

  return (
    <canvas
      ref={canvasRef}
      style={{ width: "100%", height, cursor: "grab", touchAction: "none" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    />
  );
}
