/**
 * Task centre state: background tasks this browser tracks, merged (``mergeServerTasks``) with the caller's recent
 * tasks from ``GET /api/tasks`` so tasks started elsewhere (another tab/device, a page that did not track) show too.
 * A task is tracked when any TanStack mutation resolves with an ``AsyncTaskAccepted`` body (see TaskCenter), or when
 * a page calls ``trackTask`` itself. Tracking streams progress with ``awaitTask``; ids survive a reload (persisted)
 * and tracking resumes. Dismissing stops listening only — it never cancels the server-side task; cancel is explicit.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { awaitTask, cancelTask, getTask, thinkingSteps, type TaskProgressEvent, type TaskState, type ThinkingStep } from "@/api/stream";
import { notifyDesktop } from "@/desktop/notify";

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
  /** Known only from the server list (not followed by this browser). */
  server?: boolean;
}

/** One row of ``GET /api/tasks``. */
export interface ServerTask {
  task_id: string;
  kind: string;
  state: TaskState;
  progress: number;
  message: string;
  stage: string;
  stream_url: string;
  elapsed_ms?: number | null;
  owner_id?: string | null;
  updated_at: string;
}

/**
 * Local tracked tasks + server tasks, newest first. A locally tracked task keeps its title/link/steps but takes a
 * terminal state from the server when the local copy is still non-terminal (its watcher was lost). Server-only
 * tasks the user dismissed (``hidden``) are left out.
 */
export function mergeServerTasks(local: TrackedTask[], server: ServerTask[], hidden: readonly string[] = []): TrackedTask[] {
  const hide = new Set(hidden);
  const byId = new Map(server.map((s) => [s.task_id, s]));
  const merged: TrackedTask[] = local.map((t) => {
    const s = byId.get(t.id);
    if (!s || isTerminal(t.state) || !isTerminal(s.state)) return t;
    return { ...t, state: s.state, progress: s.progress, message: s.message || t.message, stage: s.stage || t.stage };
  });
  const known = new Set(local.map((t) => t.id));
  for (const s of server) {
    if (known.has(s.task_id) || hide.has(s.task_id)) continue;
    const at = Date.parse(s.updated_at);
    merged.push({
      id: s.task_id, kind: s.kind, state: s.state, progress: s.progress, message: s.message, stage: s.stage || undefined,
      steps: [], startedAt: Number.isNaN(at) ? 0 : at, server: true,
      ...(isTerminal(s.state) && !Number.isNaN(at) ? { finishedAt: at } : {}),
    });
  }
  return merged.sort((a, b) => b.startedAt - a.startedAt);
}

interface TaskCenterState {
  tasks: TrackedTask[];
  /** Server-listed task ids the user dismissed from the centre. */
  hidden: string[];
  hide: (ids: string[]) => void;
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
      hidden: [],
      hide: (ids) => set((s) => ({ hidden: [...new Set([...ids, ...s.hidden])].slice(0, 300) })),
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
    { name: "formunex-task-centre", partialize: (s) => ({ tasks: s.tasks.map((t) => ({ ...t, steps: [] })), hidden: s.hidden }) },
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
      const before = useTaskCenter.getState().tasks.find((t) => t.id === id);
      if (isTerminal(state) && before && !isTerminal(before.state)) {
        void notifyDesktop(before.title || before.kind || "FormuNex", ev.message || state);
      }
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
  if (useTaskCenter.getState().hidden.includes(id)) useTaskCenter.setState((s) => ({ hidden: s.hidden.filter((x) => x !== id) }));
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
  // also keep it out of the server list merge
  useTaskCenter.getState().hide([id]);
}

export async function cancelTracked(id: string) {
  await cancelTask(id);
  useTaskCenter.getState().upsert({ id, message: "…" });
}
