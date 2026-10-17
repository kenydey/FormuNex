import { App } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, type AsyncTaskAccepted } from "@/api/stream";
import { trackTask } from "@/features/chat/taskStore";
import { errorText } from "@/shared/feedback";

export const reparsePath = (id: string) => `/api/kb/sources/${encodeURIComponent(id)}/reparse`;

/**
 * "重新解析": queue ``POST /api/kb/sources/{id}/reparse`` (re-parse the retained original with the current parse
 * profile, rebuilding chunks / index / ToC tree), show it in the header task centre, and refresh every ``kb`` query
 * once it finishes. A source without a retained original answers 409 with the reason, shown as an error toast.
 */
export function useReparse() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [running, setRunning] = useState<Record<string, true>>({});
  const done = (id: string) => setRunning((r) => {
    const next = { ...r };
    delete next[id];
    return next;
  });
  const m = useMutation({
    mutationFn: (id: string) => call<AsyncTaskAccepted>("POST", reparsePath(id)),
    onSuccess: (acc, id) => {
      trackTask(acc.task_id, { title: t("library.sources.reparse_task", "重新解析文档"), link: `/knowledge/sources/${id}` });
      message.info(t("library.sources.reparse_queued", "已提交重新解析，完成后自动刷新"));
      setRunning((r) => ({ ...r, [id]: true }));
      awaitTask(acc.task_id)
        .then((final) => {
          message.success(final.message || t("library.sources.reparse_done", "重新解析完成"));
          void qc.invalidateQueries({ queryKey: ["kb"] });
        })
        .catch((e: unknown) => message.error(t("library.sources.reparse_failed", "重新解析失败：{{e}}", { e: errorText(e) })))
        .finally(() => done(id));
    },
    onError: (e) => message.error(errorText(e)),
  });
  return {
    reparse: (id: string) => m.mutate(id),
    isRunning: (id: string) => !!running[id] || (m.isPending && m.variables === id),
  };
}
