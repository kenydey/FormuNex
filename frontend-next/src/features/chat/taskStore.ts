/**
 * Task centre state: background tasks this browser knows about (the backend has no "list my tasks" endpoint).
 * A task is tracked when any TanStack mutation resolves with an ``AsyncTaskAccepted`` body (see TaskCenter), or when
 * a page calls ``trackTask`` itself. Tracking streams progress with ``awaitTask``; ids survive a reload (persisted)
 * and tracking resumes. Dismissing stops listening only — it never cancels the server-side task; cancel is explicit.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { awaitTask, cancelTask, getTask, thinkingSteps, type TaskProgressEvent, type TaskState, type ThinkingStep } from "@/api/stream";

export interface TrackedTask {
  id: string;
  title?: string;
  kind?: string;
  link?: string;
  state: TaskState;
  progress: number;
  message: string;
  stage?: string;
  steps: ThinkingStep[];
  startedAt: number;
  finishedAt?: number;
  /** A finished task the user has not opened the centre since. */
  unseen?: boolean;
}

interface TaskCenterState {
  tasks: TrackedTask[];
  upsert: (t: Partial<TrackedTask> & { id: string }) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
  markSeen: () => void;
}

const STATE: Record<TaskProgressEvent["status"], TaskState> = {
  PENDING: "pending", RUNNING: "running", COMPLETED: "completed", FAILED: "failed", CANCELLED: "cancelled",
};
export const isTerminal = (s: TaskState) => s === "completed" || s === "failed" || s === "cancelled";

export const useTaskCenter = create<TaskCenterState>()(
  persist(
    (set) => ({
      tasks: [],
      upsert: (t) => set((s) => {
        const i = s.tasks.findIndex((x) => x.id === t.id);
        if (i < 0) {
          const fresh: TrackedTask = { state: "pending", progress: 0, message: "", steps: [], startedAt: Date.now(), ...t };
          return { tasks: [fresh, ...s.tasks].slice(0, 50) };
        }
        const tasks = s.tasks.slice();
        tasks[i] = { ...tasks[i], ...t };
        return { tasks };
      }),
      remove: (id) => set((s) => ({ tasks: s.tasks.filter((x) => x.id !== id) })),
      clearFinished: () => set((s) => ({ tasks: s.tasks.filter((x) => !isTerminal(x.state)) })),
      markSeen: () => set((s) => ({ tasks: s.tasks.map((x) => (x.unseen ? { ...x, unseen: false } : x)) })),
    }),
    { name: "formunex-task-centre", partialize: (s) => ({ tasks: s.tasks.map((t) => ({ ...t, steps: [] })) }) },
  ),
);

const watchers = new Map<string, AbortController>();

function watch(id: string) {
  if (watchers.has(id)) return;
  const ctrl = new AbortController();
  watchers.set(id, ctrl);
  const { upsert } = useTaskCenter.getState();
  void getTask(id).then((s) => upsert({ id, kind: s.kind })).catch(() => undefined);
  awaitTask(id, {
    signal: ctrl.signal,
    onEvent: (ev) => {
      const state = STATE[ev.status] ?? "running";
      const steps = thinkingSteps(ev);
      upsert({ id, state, progress: ev.progress ?? 0, message: ev.message, stage: ev.stage,
        ...(steps.length ? { steps } : {}),
        ...(isTerminal(state) ? { finishedAt: Date.now(), unseen: true } : {}) });
    },
  })
    .catch((e: unknown) => {
      if (ctrl.signal.aborted) return;
      const cur = useTaskCenter.getState().tasks.find((t) => t.id === id);
      if (cur && !isTerminal(cur.state)) upsert({ id, state: "failed", message: e instanceof Error ? e.message : String(e), finishedAt: Date.now(), unseen: true });
    })
    .finally(() => watchers.delete(id));
}

/** Show a background task in the header task centre and follow its progress. */
export function trackTask(id: string, opts: { title?: string; link?: string } = {}) {
  const exists = useTaskCenter.getState().tasks.some((t) => t.id === id);
  useTaskCenter.getState().upsert({ id, ...opts, ...(exists ? {} : { state: "pending" as TaskState }) });
  watch(id);
}

/** Resume following non-terminal tasks after a reload. */
export function resumeTracking() {
  for (const t of useTaskCenter.getState().tasks) if (!isTerminal(t.state)) watch(t.id);
}

export function dismissTask(id: string) {
  watchers.get(id)?.abort();
  watchers.delete(id);
  useTaskCenter.getState().remove(id);
}

export async function cancelTracked(id: string) {
  await cancelTask(id);
  useTaskCenter.getState().upsert({ id, message: "…" });
}
