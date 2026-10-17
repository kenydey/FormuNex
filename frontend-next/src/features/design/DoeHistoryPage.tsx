import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Drawer, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { DispatchButton } from "@/features/lab/DispatchButton";
import { planActions } from "./logic";
import type { DoeHistoryItem, WorkbenchCampaignSummary } from "./types";

interface HistoryPage { items: DoeHistoryItem[]; total: number; page: number; page_size: number }
interface PauseStatus { isPaused: boolean; pausedUntil?: string | null; lapsedAt?: string | null; degraded?: boolean }

const STATUS_COLOR: Record<string, string> = { draft: "default", active: "green", completed: "blue", aborted: "red" };

export function DoeStatusTag({ status }: { status?: string }) {
  const { t } = useTranslation();
  const s = status || "draft";
  const label = ({
    draft: t("design.hist.s.draft", "草稿"), active: t("design.hist.s.active", "执行中"),
    completed: t("design.hist.s.completed", "已完成"), aborted: t("design.hist.s.aborted", "已终止"),
  } as Record<string, string>)[s] ?? s;
  return <Tag color={STATUS_COLOR[s] ?? "default"}>{label}</Tag>;
}

/** Pause / resume the closed-loop DOE cycle of one workbench campaign. */
function CyclePause({ campaignId }: { campaignId: number }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [ttl, setTtl] = useState<number | null>(24);
  const key = ["design", "doe-pause", campaignId];
  const status = useQuery({ queryKey: key, queryFn: () => call<PauseStatus>("GET", `/api/experiments/hooks/doecyle-status/${campaignId}`) });
  const toggle = useMutation({
    mutationFn: (pause: boolean) => call("POST", `/api/experiments/hooks/pause-doecycle/${campaignId}`,
      pause ? { isPaused: true, ttlHours: ttl ?? 0 } : { isPaused: false }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
    onError: (e) => message.error(errorText(e)),
  });
  const paused = !!status.data?.isPaused;
  return (
    <Space wrap>
      <Tag color={paused ? "orange" : "green"}>{paused ? t("design.hist.paused", "闭环已暂停") : t("design.hist.running", "闭环运行中")}</Tag>
      {paused && status.data?.pausedUntil && <Typography.Text type="secondary">
        {t("design.hist.paused_until", "至 {{t}}", { t: status.data.pausedUntil.slice(0, 19).replace("T", " ") })}</Typography.Text>}
      {!paused && status.data?.lapsedAt && <Typography.Text type="secondary">
        {t("design.hist.lapsed", "上次暂停已于 {{t}} 自动恢复", { t: status.data.lapsedAt.slice(0, 19).replace("T", " ") })}</Typography.Text>}
      {status.data?.degraded && <Tag color="warning">{t("design.hist.degraded", "状态存储不可用")}</Tag>}
      {paused ? (
        <Button size="small" loading={toggle.isPending} onClick={() => toggle.mutate(false)}>{t("design.hist.resume", "恢复闭环")}</Button>
      ) : (
        <>
          <InputNumber size="small" min={0} max={720} value={ttl} onChange={setTtl} addonAfter={t("design.hist.hours", "小时")} style={{ width: 130 }} />
          <Button size="small" loading={toggle.isPending} onClick={() => toggle.mutate(true)}>{t("design.hist.pause", "暂停闭环")}</Button>
        </>
      )}
    </Space>
  );
}

/** DOE plan history (legacy DoeHistoryPanel): per project, optionally one campaign; lifecycle draft → active → completed / aborted. */
export default function DoeHistoryPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [campaignId, setCampaignId] = useState<number | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [detail, setDetail] = useState<DoeHistoryItem | null>(null);
  const [abortFor, setAbortFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const campaigns = useQuery({ queryKey: ["workbench", "campaigns"],
    queryFn: () => call<WorkbenchCampaignSummary[]>("GET", "/api/experiments/workbench/campaigns") });
  const qs = new URLSearchParams({ project_id: projectId ?? "", page: String(page), page_size: String(pageSize) });
  if (campaignId != null) qs.set("campaign_id", String(campaignId));
  const history = useQuery({ queryKey: ["design", "doe-history", qs.toString()], enabled: !!projectId,
    queryFn: () => call<HistoryPage>("GET", `/api/doe/history?${qs}`) });

  const transition = useMutation({
    mutationFn: ({ id, action, why }: { id: string; action: "activate" | "complete" | "abort"; why?: string }) =>
      call("POST", `/api/doe/${encodeURIComponent(id)}/${action}`, action === "abort" ? { reason: why ?? "" } : {}),
    onSuccess: () => { setAbortFor(null); setReason(""); void qc.invalidateQueries({ queryKey: ["design", "doe-history"] }); },
    onError: (e) => message.error(errorText(e)),
  });

  const actionLabel = { activate: t("design.hist.activate", "启动执行"), complete: t("design.hist.complete", "标记完成"),
    abort: t("design.hist.abort", "终止") };

  if (!projectId) {
    return <PageContainer><Alert type="info" showIcon message={t("design.hist.need_project", "请先选择项目（DOE 历史按项目隔离）。")} /></PageContainer>;
  }
  return (
    <PageContainer subTitle={t("design.hist.sub", "DOE 计划按项目隔离，最新优先")}
      extra={[<Link key="c" to="/lab/doe/classic"><Button>{t("design.hist.classic", "经典 DOE")}</Button></Link>]}>
      <ErrorAlert error={history.error} />
      {campaignId != null && <ProCard bordered size="small" style={{ marginBottom: 16 }}><CyclePause campaignId={campaignId} /></ProCard>}
      <ProTable<DoeHistoryItem>
        rowKey={(r) => String(r.plan_id ?? `${r.design}-${r.created_at}`)} search={false} loading={history.isLoading}
        dataSource={history.data?.items} options={{ reload: () => void history.refetch() }}
        headerTitle={<Space wrap>
          <span>{t("design.hist.scope", "实验台账")}</span>
          <Select allowClear style={{ width: 260 }} value={campaignId} placeholder={t("design.hist.all", "全部")}
            onChange={(v) => { setCampaignId(v); setPage(1); }} loading={campaigns.isLoading}
            options={(campaigns.data ?? []).filter((c) => c.project_id === projectId).map((c) => ({ value: c.id, label: `#${c.id} ${c.name}` }))} />
        </Space>}
        pagination={{ current: page, pageSize, total: history.data?.total ?? 0, showSizeChanger: true,
          onChange: (p, s) => { setPage(p); setPageSize(s); } }}
        columns={[
          { title: t("design.hist.design", "设计"), dataIndex: "design", render: (_d, r) => <a onClick={() => setDetail(r)}>{r.design || "—"}</a> },
          { title: t("design.hist.round", "轮次"), dataIndex: "round", width: 70, render: (_d, r) => (r.round != null ? `R${r.round}` : "—") },
          { title: t("design.hist.status", "状态"), dataIndex: "status", width: 100, render: (_d, r) => <DoeStatusTag status={r.status} /> },
          { title: t("design.hist.runs", "实验数"), width: 80, render: (_d, r) => (Array.isArray(r.runs) ? r.runs.length : 0) },
          { title: t("design.hist.campaign", "台账"), dataIndex: "campaign_id", width: 80, render: (_d, r) => (r.campaign_id != null ? `#${r.campaign_id}` : "—") },
          { title: t("design.hist.notes", "备注"), dataIndex: "notes", ellipsis: true },
          { title: t("common.created", "创建时间"), dataIndex: "created_at", width: 170,
            render: (_d, r) => (r.created_at ? r.created_at.slice(0, 19).replace("T", " ") : "—") },
          { title: t("common.actions", "操作"), valueType: "option", width: 200, render: (_d, r) => r.plan_id ? planActions(r.status).map((a) => (
            <a key={a} onClick={() => (a === "abort" ? setAbortFor(r.plan_id!) : transition.mutate({ id: r.plan_id!, action: a }))}>{actionLabel[a]}</a>
          )) : [] },
        ]}
      />
      <Modal open={abortFor != null} title={t("design.hist.abort_title", "终止 DOE 计划")} onCancel={() => setAbortFor(null)}
        confirmLoading={transition.isPending} okButtonProps={{ danger: true }}
        onOk={() => abortFor && transition.mutate({ id: abortFor, action: "abort", why: reason })}>
        <Input.TextArea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("design.hist.reason", "终止原因（可空）")} />
      </Modal>
      <Drawer open={!!detail} onClose={() => setDetail(null)} width={Math.min(900, window.innerWidth - 32)}
        title={detail ? `${detail.design ?? ""} · ${detail.plan_id?.slice(0, 8) ?? ""}` : ""}
        extra={detail?.plan_id && <Space>
          <Button onClick={() => download(`/api/doe/${encodeURIComponent(detail.plan_id!)}/export?format=csv`, `doe_${detail.plan_id}.csv`)
            .catch((e) => message.error(errorText(e)))}>{t("design.doe.export_csv", "导出 CSV")}</Button>
          <DispatchButton planId={detail.plan_id} />
        </Space>}>
        {detail && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
              <Descriptions.Item label={t("design.hist.status", "状态")}><DoeStatusTag status={detail.status} /></Descriptions.Item>
              <Descriptions.Item label={t("design.hist.domain", "产品域")}>{detail.domain || "—"}</Descriptions.Item>
              <Descriptions.Item label={t("design.hist.notes", "备注")} span={2}>
                <span style={{ whiteSpace: "pre-wrap" }}>{detail.notes || "—"}</span></Descriptions.Item>
            </Descriptions>
            <Table size="small" rowKey="run_id" dataSource={detail.runs ?? []} pagination={false} scroll={{ x: true }}
              columns={[{ title: "#", dataIndex: "run_id", width: 60 },
                ...(detail.factors ?? []).map((f) => ({ title: f.name, key: f.name, align: "right" as const,
                  render: (_v: unknown, r: NonNullable<DoeHistoryItem["runs"]>[number]) => {
                    const v = r.natural?.[f.name];
                    return typeof v === "number" ? Number(v.toFixed(4)) : String(v ?? "—");
                  } }))]} />
          </>
        )}
      </Drawer>
    </PageContainer>
  );
}
