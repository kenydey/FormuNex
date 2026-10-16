import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { App, Button, Descriptions, Select, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { LabTask } from "./types";

interface Conflict { id: string; connector: string; external_id: string; external_url: string; task_no: string | null; local: Record<string, number>; remote: Record<string, number>; created_at: string }
interface Connector { name: string; configured: boolean; can_push: boolean }

/** 外部 ELN (方案 §5.3.1): push tasks to eLabFTW, pull values into the review queue, resolve conflicts. */
export default function ElnPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const [connector, setConnector] = useState("elabftw");
  const [selected, setSelected] = useState<string[]>([]);
  const connectors = useQuery({ queryKey: ["lab", "eln", "connectors"], queryFn: () => call<Connector[]>("GET", "/api/lab/eln/connectors") });
  const tasks = useQuery({ queryKey: ["lab", "tasks", "eln", projectId], queryFn: () => call<LabTask[]>("GET", `/api/lab/tasks${projectId ? `?project_id=${projectId}` : ""}`) });
  const conflicts = useQuery({ queryKey: ["lab", "eln", "conflicts"], queryFn: () => call<Conflict[]>("GET", "/api/lab/eln/conflicts") });
  const done = () => void qc.invalidateQueries({ queryKey: ["lab"] });
  const push = useMutation({
    mutationFn: () => call<{ pushed: unknown[]; skipped: string[]; errors: { error: string }[] }>("POST", `/api/lab/eln/${connector}/push`, { task_ids: selected }),
    onSuccess: (o) => { message.success(t("eln.pushed", "推送 {{n}}，跳过 {{s}}", { n: o.pushed.length, s: o.skipped.length })); o.errors.forEach((e) => message.error(e.error)); done(); },
    onError: (e) => message.error(errorText(e)),
  });
  const pull = useMutation({
    mutationFn: () => call<{ checked: number; recorded: number; conflicts: number; unchanged: number }>("POST", `/api/lab/eln/${connector}/pull`, {}),
    onSuccess: (o) => { message.success(t("eln.pulled", "检查 {{c}}，新记录 {{r}}（待复核），冲突 {{x}}", { c: o.checked, r: o.recorded, x: o.conflicts })); done(); },
    onError: (e) => message.error(errorText(e)),
  });
  const resolve = useMutation({
    mutationFn: ({ id, resolution }: { id: string; resolution: string }) => call("POST", `/api/lab/eln/conflicts/${id}/resolve`, { resolution }),
    onSuccess: done, onError: (e) => message.error(errorText(e)),
  });
  const current = connectors.data?.find((c) => c.name === connector);
  return (
    <PageContainer subTitle={t("eln.sub", "FormuNex 是唯一真源：外部 ELN 回读的数值进入复核队列，不直接用于发布")}>
      <ProCard bordered>
        <Space wrap>
          <Select value={connector} onChange={setConnector} style={{ width: 160 }} options={(connectors.data ?? []).map((c) => ({ value: c.name, label: c.name }))} />
          {current && <Descriptions size="small" column={2}><Descriptions.Item label={t("connectors.state", "状态")}>{current.configured ? <Tag color="green">{t("connectors.ok", "已配置")}</Tag> : <Tag>{t("connectors.not_configured", "未配置")}</Tag>}</Descriptions.Item></Descriptions>}
          <Button disabled={!current?.can_push || !selected.length || !can("doe.write")} loading={push.isPending} onClick={() => push.mutate()}>{t("eln.push", "推送所选任务单")}</Button>
          <Button type="primary" disabled={!can("lab.review")} loading={pull.isPending} onClick={() => pull.mutate()}>{t("eln.pull", "回读数值")}</Button>
        </Space>
      </ProCard>
      <ProTable<LabTask> style={{ marginTop: 16 }} rowKey="id" search={false} options={false} dataSource={tasks.data}
        rowSelection={{ selectedRowKeys: selected, onChange: (k) => setSelected(k as string[]) }}
        headerTitle={t("eln.tasks", "任务单")}
        columns={[{ title: t("tasks.no", "任务单"), dataIndex: "task_no" }, { title: t("tasks.sample", "样品号"), dataIndex: "sample_no" },
          { title: t("tasks.status", "状态"), render: (_d, r) => <StatusTag status={r.status} /> }]} />
      <ProTable<Conflict> style={{ marginTop: 16 }} rowKey="id" search={false} options={false} dataSource={conflicts.data}
        headerTitle={t("eln.conflicts", "同步冲突（两侧都改过）")}
        columns={[
          { title: t("tasks.no", "任务单"), dataIndex: "task_no" },
          { title: t("eln.remote_record", "外部记录"), render: (_d, r) => r.external_url ? <a href={r.external_url} target="_blank" rel="noreferrer">{r.connector} #{r.external_id}</a> : `${r.connector} #${r.external_id}` },
          { title: t("eln.local", "FormuNex"), render: (_d, r) => JSON.stringify(r.local) },
          { title: t("eln.remote", "外部"), render: (_d, r) => JSON.stringify(r.remote) },
          { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => can("lab.review") ? [
            <a key="l" onClick={() => resolve.mutate({ id: r.id, resolution: "keep_local" })}>{t("eln.keep_local", "保留本地")}</a>,
            <a key="r" onClick={() => resolve.mutate({ id: r.id, resolution: "take_remote" })}>{t("eln.take_remote", "采用外部（进入复核）")}</a>] : [] },
        ]} />
    </PageContainer>
  );
}
