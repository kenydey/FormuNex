import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Empty, Table, Tag, Typography, Upload, theme } from "antd";
import { CheckCircleTwoTone, CloseCircleTwoTone, SyncOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { fx } from "./api";
import { useSession } from "@/app/session";
import { datalabSyncOutcome } from "./logic";
import type { Attachment, QCMeasurement } from "./types";

/** One training experiment: measured snapshot, typed QC measurements, attachments, Datalab sync (legacy ExperimentsBrowser detail). */
export default function ExperimentDetailPage() {
  const { can } = useSession();
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [syncMsg, setSyncMsg] = useState<{ type: "success" | "warning"; text: string } | null>(null);
  const summary = useQuery({ queryKey: ["formulation", "experiments", "one", id], enabled: !!id, queryFn: () => fx.experiment(id) });
  const meas = useQuery({ queryKey: ["formulation", "exp-meas", id], queryFn: () => fx.experimentMeasurements(id) });
  const atts = useQuery({ queryKey: ["formulation", "exp-atts", id], queryFn: () => fx.experimentAttachments(id) });
  const upload = useMutation({
    mutationFn: (file: File) => fx.uploadExperimentAttachment(id, file),
    onSuccess: () => { message.success(t("formulation.att.uploaded", "已上传")); void qc.invalidateQueries({ queryKey: ["formulation", "exp-atts", id] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const sync = useMutation({
    mutationFn: () => fx.syncDatalab(id),
    onSuccess: (r) => {
      const o = datalabSyncOutcome(r);
      setSyncMsg(o.skipped
        ? { type: "warning", text: t("formulation.exp.sync_skipped", "Datalab 不可达，已跳过（fail-open），本地数据未动。") }
        : { type: "success", text: t("formulation.exp.sync_done", "同步完成：{{n}} 条测量已回写，{{e}} 项失败。", { n: o.synced, e: o.errors }) });
      void qc.invalidateQueries({ queryKey: ["formulation", "exp-meas", id] });
      void qc.invalidateQueries({ queryKey: ["formulation", "experiments"] });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const s = summary.data;
  const measured = Object.entries(s?.measured ?? {});

  return (
    <PageContainer
      title={t("formulation.exp.detail_title", "实验 #{{id}}", { id })}
      subTitle={s?.label || undefined}
      extra={<Button icon={<SyncOutlined />} disabled={!can("optimize.run")} loading={sync.isPending} onClick={() => sync.mutate()}>
        {t("formulation.exp.sync", "从 Datalab 同步")}</Button>}
    >
      <ErrorAlert error={summary.error ?? meas.error ?? atts.error} />
      {syncMsg && <Alert type={syncMsg.type} showIcon closable message={syncMsg.text} style={{ marginBottom: 12 }} onClose={() => setSyncMsg(null)} />}
      <ProCard direction="column" gutter={[0, 16]} ghost>
        <ProCard title={t("formulation.exp.summary", "实测摘要")} bordered loading={summary.isLoading}>
          {s && <Descriptions size="small" column={{ xs: 1, md: 3 }} style={{ marginBottom: 12 }} items={[
            { key: "d", label: t("formulation.exp.domain", "产品领域"), children: s.domain },
            { key: "s", label: t("formulation.exp.source", "来源"), children: s.source || "—" },
            { key: "c", label: t("common.created", "创建时间"), children: s.created_at?.slice(0, 16).replace("T", " ") ?? "—" },
          ]} />}
          {measured.length === 0
            ? <Typography.Text type="secondary">{t("formulation.exp.no_measured", "无 measured 快照（可能仅有结构化测量）")}</Typography.Text>
            : <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} items={measured.map(([k, v]) => ({ key: k, label: k, children: <Typography.Text code>{v}</Typography.Text> }))} />}
        </ProCard>
        <ProCard title={t("formulation.exp.typed", "结构化测量 · {{n}}", { n: meas.data?.measurements?.length ?? 0 })} bordered>
          <MeasurementTable rows={meas.data?.measurements ?? []} loading={meas.isLoading} />
        </ProCard>
        <ProCard title={t("formulation.att.title", "附件 · {{n}}", { n: atts.data?.length ?? 0 })} bordered
          extra={<Upload showUploadList={false} beforeUpload={(f) => { upload.mutate(f); return false; }}>
            <Button icon={<UploadOutlined />} disabled={!can("optimize.run")} loading={upload.isPending}>{t("formulation.att.upload", "上传")}</Button>
          </Upload>}>
          <Table<Attachment> size="small" rowKey="id" pagination={false} loading={atts.isLoading} dataSource={atts.data}
            locale={{ emptyText: <Empty description={t("formulation.att.empty", "暂无附件")} /> }}
            columns={[
              { title: t("formulation.att.kind", "类型"), dataIndex: "kind", width: 120, render: (k: string) => <Tag>{k || "file"}</Tag> },
              { title: t("formulation.att.file", "文件"), render: (_d, a) => a.filename || a.source_document_id },
              { title: t("formulation.att.note", "备注"), dataIndex: "note", ellipsis: true },
              { title: t("common.created", "创建时间"), dataIndex: "created_at", width: 170, render: (v?: string | null) => v?.slice(0, 16).replace("T", " ") ?? "—" },
            ]} />
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

export function MeasurementTable({ rows, loading }: { rows: QCMeasurement[]; loading?: boolean }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  return (
    <Table<QCMeasurement> size="small" pagination={false} loading={loading} dataSource={rows.map((m, i) => ({ ...m, _key: `${m.metric}-${i}` }))} rowKey="_key"
      locale={{ emptyText: <Empty description={t("formulation.exp.no_typed", "暂无结构化测量")} /> }}
      columns={[
        { title: t("formulation.exp.metric", "指标"), dataIndex: "metric" },
        { title: t("formulation.exp.value", "数值"), render: (_d, m) => `${m.value}${m.unit ? ` ${m.unit}` : ""}` },
        { title: t("formulation.exp.method", "方法"), dataIndex: "test_method", render: (v: string) => v || "—" },
        { title: t("formulation.exp.spec", "规格"), render: (_d, m) => m.spec_min == null && m.spec_max == null ? "—" : `${m.spec_min ?? ""} – ${m.spec_max ?? ""}` },
        { title: t("formulation.exp.passed", "判定"), width: 80, render: (_d, m) => m.passed === true
          ? <CheckCircleTwoTone twoToneColor={token.colorSuccess} /> : m.passed === false ? <CloseCircleTwoTone twoToneColor={token.colorError} /> : "—" },
      ]} />
  );
}

