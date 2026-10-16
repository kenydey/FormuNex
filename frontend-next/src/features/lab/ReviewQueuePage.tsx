import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Input, Space } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert, Forbidden, errorText } from "@/shared/feedback";
import type { Measurement } from "./types";

/** 测量复核 (方案 §5.3.1): a second person accepts or rejects each value; only reviewed values are used. */
export default function ReviewQueuePage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { me, can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const q = useQuery({ queryKey: ["lab", "review-queue", projectId], enabled: can("lab.review"),
    queryFn: () => call<Measurement[]>("GET", `/api/lab/review-queue${projectId ? `?project_id=${projectId}` : ""}`) });
  const review = useMutation({
    mutationFn: async ({ ids, decision, reason }: { ids: string[]; decision: "accept" | "reject"; reason?: string }) => {
      const errors: string[] = [];
      for (const id of ids) {
        try {
          await call("POST", `/api/lab/measurements/${id}/review`, { decision, reason: reason ?? "" });
        } catch (e) {
          errors.push(errorText(e));
        }
      }
      return errors;
    },
    onSuccess: (errors) => {
      errors.forEach((e) => message.error(e));
      setSelected([]);
      void qc.invalidateQueries({ queryKey: ["lab"] });
    },
  });
  if (!can("lab.review")) return <Forbidden permission="lab.review" />;
  const own = new Set((q.data ?? []).filter((m) => m.operator_id === me?.id).map((m) => m.id));
  return (
    <PageContainer subTitle={t("review.sub", "录入人不能复核自己的数据；驳回会把任务单退回录入人")}>
      <ErrorAlert error={q.error} />
      <ProTable<Measurement>
        rowKey="id" search={false} loading={q.isLoading} dataSource={q.data} options={{ reload: () => void q.refetch() }}
        rowSelection={{ selectedRowKeys: selected, onChange: (k) => setSelected(k as string[]), getCheckboxProps: (r) => ({ disabled: own.has(r.id) }) }}
        toolBarRender={() => [
          <Space key="bulk">
            <Button type="primary" disabled={!selected.length} loading={review.isPending} onClick={() => review.mutate({ ids: selected, decision: "accept" })}>{t("review.accept", "通过所选")}</Button>
            <Button danger disabled={!selected.length} onClick={() => {
              let reason = "";
              modal.confirm({ title: t("review.reject", "驳回所选"), content: <Input.TextArea onChange={(e) => { reason = e.target.value; }} placeholder={t("review.reason", "驳回理由（必填）")} />,
                onOk: () => review.mutateAsync({ ids: selected, decision: "reject", reason }) });
            }}>{t("review.reject", "驳回所选")}</Button>
          </Space>,
        ]}
        columns={[
          { title: t("tasks.no", "任务单"), dataIndex: "task_no", render: (_d, r) => r.lab_task_id ? <Link to={`/lab/tasks/${r.lab_task_id}`}>{r.task_no}</Link> : r.task_no },
          { title: t("tasks.sample", "样品号"), dataIndex: "sample_no" },
          { title: t("tasks.metric", "指标"), dataIndex: "metric" },
          { title: t("measure.value", "数值"), render: (_d, r) => `${r.value} ${r.unit}` },
          { title: t("measure.method", "方法"), dataIndex: "test_method" },
          { title: t("measure.instrument", "仪器"), dataIndex: "instrument" },
          { title: t("measure.operator", "录入人"), dataIndex: "operator" },
          { title: t("measure.note", "备注"), dataIndex: "note", ellipsis: true },
        ]}
      />
    </PageContainer>
  );
}
