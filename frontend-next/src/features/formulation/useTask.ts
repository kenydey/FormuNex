import { useRef, useState } from "react";
import { call } from "@/api/client";
import { awaitTask, cancelTask, thinkingSteps, type AsyncTaskAccepted, type TaskProgressEvent, type ThinkingStep } from "@/api/stream";
import type { ResearchResult } from "./types";

/** Submit a 202 task endpoint (recommend / AI-modify) and follow its progress; resolves to ``data.research``. */
export function useResearchTask() {
  const [busy, setBusy] = useState(false);
  const [event, setEvent] = useState<TaskProgressEvent | null>(null);
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const ctl = useRef<{ abort: AbortController; taskId?: string } | null>(null);

  async function run(path: string, body: unknown): Promise<ResearchResult> {
    const abort = new AbortController();
    ctl.current = { abort };
    setBusy(true);
    setEvent(null);
    setSteps([]);
    try {
      const { task_id } = await call<AsyncTaskAccepted>("POST", path, body);
      ctl.current.taskId = task_id;
      const final = await awaitTask(task_id, {
        signal: abort.signal,
        onEvent: (ev) => {
          setEvent(ev);
          const s = thinkingSteps(ev);
          if (s.length) setSteps(s);
        },
      });
      const research = (final.data as { research?: ResearchResult } | undefined)?.research;
      if (!research) throw new Error("任务未返回推荐结果");
      return research;
    } finally {
      setBusy(false);
      ctl.current = null;
    }
  }

  async function cancel() {
    const c = ctl.current;
    if (!c) return;
    c.abort.abort();
    if (c.taskId) await cancelTask(c.taskId).catch(() => undefined);
  }

  return { run, cancel, busy, event, steps };
}
