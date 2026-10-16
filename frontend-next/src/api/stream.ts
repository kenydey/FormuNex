/**
 * Server-sent events over fetch (方案 §3.1). ``EventSource`` cannot send the Authorization header, so streams are read
 * with ``authFetch`` (same token refresh as every other call). Used by Chat (POST /api/chat/stream) and background
 * tasks (GET /api/tasks/{id}/stream, with a polling fallback when the stream drops).
 */
import { authFetch } from "./auth";
import { ApiError } from "./client";

export type TaskState = "pending" | "running" | "completed" | "failed" | "cancelled";
export type TaskProgressStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface ThinkingStep {
  id: string;
  kind?: "stage" | "thought" | "tool";
  title: string;
  detail?: string;
  status?: "pending" | "running" | "done" | "error";
}

export interface TaskProgressEvent {
  status: TaskProgressStatus;
  stage?: string;
  message: string;
  progress?: number;
  data?: Record<string, unknown> & { thinking?: ThinkingStep[] };
  elapsed_ms?: number | null;
}

export interface TaskStatus {
  task_id: string;
  kind: string;
  state: TaskState;
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
  stage: string;
  elapsed_ms?: number | null;
}

/** HTTP 202 body of endpoints that enqueue a background task. */
export interface AsyncTaskAccepted {
  task_id: string;
  stream_url?: string;
  status_url?: string;
}

export function thinkingSteps(ev: TaskProgressEvent | null | undefined): ThinkingStep[] {
  const raw = ev?.data?.thinking;
  return Array.isArray(raw) ? raw.filter((s) => s && typeof s === "object" && typeof s.id === "string") : [];
}

/** Read ``data:`` frames from an SSE response body; resolves at end of stream. */
export async function readSse(res: Response, onData: (data: unknown) => void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const payload = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
      if (!payload) continue; // heartbeat comments
      try {
        onData(JSON.parse(payload));
      } catch {
        // a malformed frame does not end the stream
      }
    }
  }
}

async function errorOf(res: Response): Promise<ApiError> {
  let detail: unknown = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    detail = body?.detail ?? body;
  } catch {
    // keep statusText
  }
  return new ApiError(res.status, detail);
}

/** POST a JSON body and stream the SSE answer. Rejects if the stream ends without a ``done`` / ``error`` event. */
export async function postSse<E extends { type?: string }>(path: string, body: unknown, onEvent: (ev: E) => void,
  signal?: AbortSignal): Promise<void> {
  const res = await authFetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body), signal });
  if (!res.ok) throw await errorOf(res);
  let terminated = false;
  await readSse(res, (d) => {
    const ev = d as E;
    if (ev.type === "done" || ev.type === "error") terminated = true;
    onEvent(ev);
  });
  if (!terminated && !signal?.aborted) throw new Error("流式响应中断（未收到结束事件）");
}

const FROM_STATE: Record<TaskState, TaskProgressStatus> = {
  pending: "PENDING", running: "RUNNING", completed: "COMPLETED", failed: "FAILED", cancelled: "CANCELLED",
};

export async function getTask(taskId: string): Promise<TaskStatus> {
  const res = await authFetch(`/api/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) throw await errorOf(res);
  return (await res.json()) as TaskStatus;
}

export async function cancelTask(taskId: string): Promise<void> {
  const res = await authFetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
  if (!res.ok) throw await errorOf(res);
}

/**
 * Wait for a background task, streaming its progress. A dropped stream is not a failed task (it keeps running
 * server-side): the wait falls back to polling every 2 s. ``timeoutMs = 0`` waits indefinitely.
 */
export async function awaitTask(taskId: string, opts: { onEvent?: (ev: TaskProgressEvent) => void; signal?: AbortSignal;
  timeoutMs?: number } = {}): Promise<TaskProgressEvent> {
  const { onEvent, signal, timeoutMs = 0 } = opts;
  const deadline = timeoutMs > 0 ? Date.now() + timeoutMs : Infinity;
  let last: TaskProgressEvent | null = null;
  const terminal = (ev: TaskProgressEvent) => ev.status === "COMPLETED" || ev.status === "FAILED" || ev.status === "CANCELLED";
  const settle = (ev: TaskProgressEvent) => {
    if (ev.status === "COMPLETED") return ev;
    throw new Error(ev.message || (ev.status === "CANCELLED" ? "任务已取消" : "任务失败"));
  };
  try {
    const res = await authFetch(`/api/tasks/${encodeURIComponent(taskId)}/stream`, { headers: { Accept: "text/event-stream" }, signal });
    if (res.ok) {
      await readSse(res, (d) => {
        last = d as TaskProgressEvent;
        onEvent?.(last);
      });
      if (last && terminal(last)) return settle(last);
    }
  } catch (err) {
    if (signal?.aborted) throw new Error("已停止等待");
    void err; // fall through to polling
  }
  for (;;) {
    if (signal?.aborted) throw new Error("已停止等待");
    if (Date.now() > deadline) throw new Error(`任务超时（${Math.round(timeoutMs / 1000)} 秒），后台仍在运行`);
    const s = await getTask(taskId);
    const ev: TaskProgressEvent = { status: FROM_STATE[s.state] ?? "FAILED", message: s.message, progress: s.progress,
      stage: s.stage, data: s.result ?? undefined, elapsed_ms: s.elapsed_ms };
    onEvent?.(ev);
    if (terminal(ev)) return settle(ev);
    await new Promise((r) => setTimeout(r, 2000));
  }
}
