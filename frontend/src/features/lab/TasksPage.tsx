import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Segmented, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { LabTask } from "./types";

/** 实验任务看板 (方案 §5.3.1): tasks generated from DOE plans; claim, enter, submit. */
export default function TasksPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { me, can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const [params] = useSearchParams();
  const plan = params.get("plan");
  const [scope, setScope] = useState<"all" | "mine" | "open">(plan ? "all" : "open");
  const qc = useQueryClient();
  const qs = new URLSearchParams();
  if (plan) qs.set("doe_plan_id", plan);
  else if (projectId) qs.set("project_id", projectId);
  if (scope === "mine" && me) qs.set("assignee_id", me.id);
  if (scope === "open") qs.set("status", "open");
  const tasks = useQuery({ queryKey: ["lab", "tasks", qs.toString()], queryFn: () => call<LabTask[]>("GET", `/api/lab/tasks?${qs}`) });
  const claim = useMutation({
    mutationFn: (id: string) => call("POST", `/api/lab/tasks/${id}/claim`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["lab", "tasks"] }),
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <PageContainer subTitle={plan ? t("tasks.plan", "DOE 计划 {{id}}", { id: plan.slice(0, 8) }) : undefined}>
      <ErrorAlert error={tasks.error} />
      <ProTable<LabTask>
        rowKey="id" search={false} loading={tasks.isLoading} dataSource={tasks.data} options={{ reload: () => void tasks.refetch() }}
        headerTitle={<Segmented value={scope} onChange={(v) => setScope(v as typeof scope)} options={[
          { value: "open", label: t("tasks.open", "待领取") }, { value: "mine", label: t("tasks.mine", "我的") }, { value: "all", label: t("tasks.all", "全部") }]} />}
        columns={[
          { title: t("tasks.no", "任务单"), dataIndex: "task_no", render: (_d, r) => <Link to={`/lab/tasks/${r.id}`}>{r.task_no}</Link> },
          { title: t("tasks.sample", "样品号"), dataIndex: "sample_no" },
          { title: t("tasks.run", "试验点"), dataIndex: "run_index", width: 80 },
          { title: t("tasks.factors", "因子设定"), dataIndex: "factors", ellipsis: true,
            render: (_d, r) => Object.entries(r.factors).map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toFixed(3)) : v}`).join("；") },
          { title: t("tasks.metrics", "待测指标"), render: (_d, r) => <Space wrap>{r.requested_metrics.map((m) => <Tag key={m.metric}>{m.metric}</Tag>)}</Space> },
          { title: t("tasks.status", "状态"), dataIndex: "status", width: 100, render: (_d, r) => <StatusTag status={r.status} /> },
          { title: t("common.actions", "操作"), valueType: "option", width: 90, render: (_d, r) =>
            r.status === "open" && can("lab.execute") ? [<a key="c" onClick={() => claim.mutate(r.id)}>{t("tasks.claim", "领取")}</a>] : [] },
        ]}
      />
    </PageContainer>
  );
}
