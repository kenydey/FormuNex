import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Card, Col, Input, Row, Segmented, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { LabTask } from "./types";

// 补齐计划 2-14: 看板按状态分组的顺序（与任务流 open → in_progress → pending_review → done 一致）。
const KANBAN_ORDER = ["open", "in_progress", "pending_review", "done", "cancelled"] as const;

/** 实验任务看板 (方案 §5.3.1): tasks generated from DOE plans; claim, enter, submit. */
export default function TasksPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { me, can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const plan = params.get("plan");
  const [scope, setScope] = useState<"all" | "mine" | "open">(plan ? "all" : "open");
  // 补齐计划 2-14: 表格 / 看板视图切换
  const [view, setView] = useState<"table" | "kanban">("table");
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
  // 补齐计划 2-14: 扫码框 —— 扫样品码（样品号）跳到对应任务单；404 提示，不跳转。
  const [scanning, setScanning] = useState(false);
  const scan = async (code: string) => {
    const sampleNo = code.trim();
    if (!sampleNo) return;
    setScanning(true);
    try {
      const out = await call<{ task?: { id: string } | null }>("GET", `/api/lab/samples/${encodeURIComponent(sampleNo)}`);
      if (out.task?.id) navigate(`/lab/tasks/${out.task.id}`);
      else message.warning(t("tasks.scan_no_task", "样品 {{no}} 未关联任务单", { no: sampleNo }));
    } catch (e) {
      message.error(t("tasks.scan_not_found", "样品 {{no}} 不存在，请核对样品码", { no: sampleNo }));
    } finally {
      setScanning(false);
    }
  };
  const grouped = new Map<string, LabTask[]>();
  for (const task of tasks.data ?? []) {
    const list = grouped.get(task.status) ?? [];
    list.push(task);
    grouped.set(task.status, list);
  }
  return (
    <PageContainer subTitle={plan ? t("tasks.plan", "DOE 计划 {{id}}", { id: plan.slice(0, 8) }) : undefined}
      extra={<Space>
        <Input.Search style={{ width: 260 }} placeholder={t("tasks.scan", "扫样品码跳到任务单")} loading={scanning} onSearch={scan} enterButton={t("tasks.scan_btn", "扫码")} />
        <Segmented value={view} onChange={(v) => setView(v as typeof view)} options={[
          { value: "table", label: t("tasks.view_table", "表格") }, { value: "kanban", label: t("tasks.view_kanban", "看板") }]} />
      </Space>}>
      <ErrorAlert error={tasks.error} />
      {view === "kanban" ? (
        <Row gutter={12}>
          {KANBAN_ORDER.map((status) => (
            <Col key={status} xs={24} sm={12} lg={8} xl={5}>
              <Card size="small" title={<Space><StatusTag status={status} /><span>({grouped.get(status)?.length ?? 0})</span></Space>} style={{ marginBottom: 12 }}>
                {(grouped.get(status) ?? []).map((r) => (
                  <Card key={r.id} size="small" style={{ marginBottom: 8 }} title={<Link to={`/lab/tasks/${r.id}`}>{r.task_no}</Link>}
                    extra={r.status === "open" && can("lab.execute", r.project_id) ? <Button size="small" loading={claim.isPending} onClick={() => claim.mutate(r.id)}>{t("tasks.claim", "领取")}</Button> : undefined}>
                    <div>{t("tasks.sample", "样品号")}：{r.sample_no}</div>
                    <div>{t("tasks.run", "试验点")}：{r.run_index ?? "—"}</div>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {Object.entries(r.factors).map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toFixed(3)) : v}`).join("；")}
                    </div>
                    <Space wrap style={{ marginTop: 4 }}>{r.requested_metrics.map((m) => <Tag key={m.metric}>{m.metric}</Tag>)}</Space>
                  </Card>
                ))}
                {(grouped.get(status)?.length ?? 0) === 0 && <div style={{ color: "#999" }}>{t("tasks.empty", "暂无")}</div>}
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
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
            r.status === "open" && can("lab.execute", r.project_id) ? [<a key="c" onClick={() => claim.mutate(r.id)}>{t("tasks.claim", "领取")}</a>] : [] },
        ]}
      />
      )}
    </PageContainer>
  );
}
