import type { LabTask } from "./types";

/**
 * 补齐计划 3-17：同一 DOE 计划内按试验点序号排序的上 / 下一任务单。
 * 让实验员在任务单之间按 DOE 矩阵顺序导航（矩阵总览在任务看板 ?plan=）。
 */
export function siblingNav(tasks: LabTask[], currentId: string): { prev: LabTask | null; next: LabTask | null } {
  const sorted = [...tasks].sort((a, b) => (a.run_index ?? Infinity) - (b.run_index ?? Infinity));
  const i = sorted.findIndex((t) => t.id === currentId);
  if (i < 0) return { prev: null, next: null };
  return { prev: sorted[i - 1] ?? null, next: sorted[i + 1] ?? null };
}
