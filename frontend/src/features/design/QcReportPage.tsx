import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Descriptions, Form, InputNumber, Radio, Select, Switch, Table, Tag, Typography, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { specRange } from "./logic";
import type { QCMeasurementView, QCReportResult, WorkbenchCampaign, WorkbenchCampaignSummary } from "./types";

const num = (s: string | null) => (s && Number(s) > 0 ? Number(s) : undefined);

/** QC report extraction (legacy QCReportModal): upload a test certificate, extract measurements with unit / method / spec window,
 *  bind them to a workbench row (or a legacy experiment). Values stage as unreviewed rows and reach training
 *  data only after review (P1-13); the sync switch only folds already-reviewed values. */
export default function QcReportPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [params, setParams] = useSearchParams();
  const [mode, setMode] = useState<"row" | "experiment">(params.get("experiment") ? "experiment" : "row");
  const campaignId = num(params.get("campaign"));
  const rowId = num(params.get("row"));
  const experimentId = num(params.get("experiment"));
  const [sync, setSync] = useState(true);
  const setParam = (patch: Record<string, number | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k);
      else next.set(k, String(v));
    }
    setParams(next, { replace: true });
  };

  const campaigns = useQuery({ queryKey: ["workbench", "campaigns"],
    queryFn: () => call<WorkbenchCampaignSummary[]>("GET", "/api/experiments/workbench/campaigns") });
  const campaign = useQuery({ queryKey: ["workbench", "campaign", campaignId], enabled: mode === "row" && campaignId != null,
    queryFn: () => call<WorkbenchCampaign>("GET", `/api/experiments/workbench/${campaignId}`) });
  const target = mode === "row" ? (campaignId && rowId ? `row:${campaignId}:${rowId}` : null) : experimentId ? `exp:${experimentId}` : null;
  const existing = useQuery({ queryKey: ["design", "qc", target], enabled: !!target,
    queryFn: () => call<{ experiment_id: number; measurements: QCMeasurementView[] }>("GET", mode === "row"
      ? `/api/qc/workbench/${campaignId}/rows/${rowId}/measurements` : `/api/qc/experiments/${experimentId}/measurements`) });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      if (mode === "row") { fd.append("campaign_id", String(campaignId)); fd.append("row_id", String(rowId)); }
      else fd.append("experiment_id", String(experimentId));
      if (projectId) fd.append("project_id", projectId);
      fd.append("sync_measured", String(sync));
      return call<QCReportResult>("POST", "/api/qc/report", fd);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["design", "qc", target] }),
  });
  const result = upload.data;
  const shown = result?.measurements.length ? result.measurements : existing.data?.measurements ?? [];

  return (
    <PageContainer subTitle={t("design.qc.sub", "上传检测报告（PDF / Word / Markdown / 图片），自动提取带单位、检测方法、规格限的计量项并绑定到实验。同一份报告重复上传不会重复计入。")}>
      <ProCard bordered>
        <Form layout="inline" style={{ rowGap: 12 }}>
          <Form.Item label={t("design.qc.bind", "绑定到")}>
            <Radio.Group optionType="button" value={mode} onChange={(e) => { setMode(e.target.value); upload.reset(); }} options={[
              { value: "row", label: t("design.qc.row", "实验台账行") }, { value: "experiment", label: t("design.qc.experiment", "实验记录 ID") }]} />
          </Form.Item>
          {mode === "row" ? (
            <>
              <Form.Item label={t("design.qc.campaign", "实验台账")}>
                <Select style={{ width: 260 }} showSearch optionFilterProp="label" value={campaignId} loading={campaigns.isLoading}
                  onChange={(v) => { setParam({ campaign: v, row: undefined }); upload.reset(); }}
                  options={(campaigns.data ?? []).filter((c) => !projectId || c.project_id === projectId)
                    .map((c) => ({ value: c.id, label: `#${c.id} ${c.name} · ${c.row_count}` }))} />
              </Form.Item>
              <Form.Item label={t("design.qc.row_label", "行")}>
                <Select style={{ width: 300 }} showSearch optionFilterProp="label" value={rowId} disabled={!campaignId} loading={campaign.isLoading}
                  onChange={(v) => { setParam({ row: v }); upload.reset(); }}
                  options={(campaign.data?.rows ?? []).map((r) => ({ value: r.id, label: `#${r.id} ${r.refcode || r.item_id || ""} · ${r.status}` }))} />
              </Form.Item>
            </>
          ) : (
            <Form.Item label={t("design.qc.experiment", "实验记录 ID")}>
              <InputNumber min={1} value={experimentId} onChange={(v) => { setParam({ experiment: v ?? undefined }); upload.reset(); }} />
            </Form.Item>
          )}
          <Form.Item label={t("design.qc.sync", "复核通过后同步进可训练数据")}><Switch checked={sync} onChange={setSync} /></Form.Item>
        </Form>
        <ErrorAlert error={campaigns.error ?? campaign.error} />
        <Upload.Dragger style={{ marginTop: 16 }} disabled={!target || upload.isPending} showUploadList={false}
          accept=".pdf,.docx,.doc,.md,.txt,.csv,.png,.jpg,.jpeg" beforeUpload={(f) => { upload.mutate(f); return false; }}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">{upload.isPending ? t("design.qc.parsing", "解析中…") : t("design.qc.drop", "点击或拖拽检测报告到此处上传并解析")}</p>
          {!target && <p className="ant-upload-hint">{t("design.qc.pick_target", "请先选择要绑定的台账行或实验")}</p>}
        </Upload.Dragger>
        <div style={{ marginTop: 12 }}><ErrorAlert error={upload.error} /></div>
        {result && (
          <>
            <Descriptions size="small" column={{ xs: 1, md: 3 }}>
              <Descriptions.Item label={t("design.qc.parser", "解析器")}>{result.parser || "—"}</Descriptions.Item>
              <Descriptions.Item label={t("design.qc.count", "提取项数")}>{result.measurement_count}</Descriptions.Item>
              <Descriptions.Item label={t("design.qc.experiment", "实验记录 ID")}>{result.experiment_id}</Descriptions.Item>
            </Descriptions>
            {result.already_attached && <Alert type="info" showIcon style={{ marginBottom: 8 }} message={t("design.qc.already", "该报告此前已绑定，未重复计入")} />}
            {result.message && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={result.message} />}
            {result.extraction_error && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={result.extraction_error} />}
            {Object.keys(result.synced_measured).length > 0 && <Alert type="success" showIcon style={{ marginBottom: 8 }}
              message={t("design.qc.synced", "已同步进可训练数据（仅复核通过值）：{{m}}", { m: Object.keys(result.synced_measured).join("、") })} />}
            {sync && result.measurement_count > 0 && Object.keys(result.synced_measured).length === 0 && !result.message && <Alert type="info" showIcon style={{ marginBottom: 8 }}
              message={t("design.qc.staged", "检测值已暂存待复核，复核通过后进入可训练数据")} />}
          </>
        )}
      </ProCard>
      <ProCard bordered style={{ marginTop: 16 }} loading={existing.isLoading}
        title={t("design.qc.measurements", "计量项（共 {{n}} 条）", { n: shown.length })}>
        <ErrorAlert error={existing.error} />
        <Table<QCMeasurementView> size="small" rowKey="_key" dataSource={shown.map((m, i) => ({ ...m, _key: `${m.metric}-${i}` }))} pagination={false} scroll={{ x: true }}
          columns={[
            { title: t("design.qc.metric", "检测项"), dataIndex: "metric" },
            { title: t("design.qc.value", "实测值"), dataIndex: "value", align: "right" },
            { title: t("design.qc.unit", "单位"), dataIndex: "unit", render: (_v, m) => m.unit || "—" },
            { title: t("design.qc.method", "检测方法"), dataIndex: "test_method", render: (_v, m) => m.test_method
              || <Typography.Text type="warning">{t("design.qc.no_method", "未注明")}</Typography.Text> },
            { title: t("design.qc.spec", "技术要求"), render: (_v, m) => specRange(m) },
            { title: t("design.qc.verdict", "判定"), render: (_v, m) => (m.passed === null
              ? <Tag>{t("design.qc.undetermined", "未判定")}</Tag>
              : m.passed ? <Tag color="green">{t("design.qc.pass", "合格")}</Tag> : <Tag color="red">{t("design.qc.fail", "超差")}</Tag>) },
          ]} />
      </ProCard>
    </PageContainer>
  );
}
