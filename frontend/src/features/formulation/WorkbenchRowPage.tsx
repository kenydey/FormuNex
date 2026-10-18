import { PageContainer, ProCard } from "@ant-design/pro-components";
import { App, Badge, Button, Descriptions, Empty, Result, Select, Space, Table, Tag, Typography, theme } from "antd";
import { EditOutlined, TagsOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { EChartsOption } from "echarts";
import { ApiError } from "@/api/client";
import { Chart } from "@/shared/Chart";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import { fx } from "./api";
import { MeasurementTable } from "./ExperimentDetailPage";
import { fmtValue, lineageTrend, planVsActual, specText, valueMeetsSpec } from "./logic";
import { NoteModal, RowAttachments, RowLineage, RowVersions, TagList, TagModal, rowKey } from "./RowPanels";
import type { ObjectiveSpec, WorkbenchRow } from "./types";

const TABS = ["overview", "attachments", "versions", "lineage"] as const;

/** One DOE workbench row: detail strip, lineage trend, QC, tags / note and every DataLab-backed action
 * (legacy RowDetailBar + ExperimentDetail + AttachmentPreview + RowVersionHistoryModal + LineageTree + NoteEditor + TagPicker). */
export default function WorkbenchRowPage() {
  const { campaignId: c = "", rowId: r = "" } = useParams();
  const campaignId = Number(c);
  const rowId = Number(r);
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? params.get("tab")! : "overview";
  const [noteOpen, setNoteOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);

  const campaign = useQuery({ queryKey: ["formulation", "campaign", c], queryFn: () => fx.campaign(c) });
  const row = campaign.data?.rows.find((x) => x.id === rowId);
  const objectives: ObjectiveSpec[] = campaign.data?.objectives_snapshot ?? [];
  const lineage = useQuery({ queryKey: [...rowKey(c, r), "lineage"], queryFn: () => fx.rowLineage(c, r) });
  const meas = useQuery({ queryKey: [...rowKey(c, r), "qc"], queryFn: () => fx.rowMeasurements(c, r) });
  const atts = useQuery({ queryKey: [...rowKey(c, r), "attachments"], queryFn: () => fx.rowAttachments(c, r) });
  // DataLab rows (refcode) and native-store rows (audit-log snapshots) both have versions; 404 = no version store.
  const versions = useQuery({ queryKey: [...rowKey(c, r), "versions"], enabled: !!row, retry: false, queryFn: () => fx.rowVersions(c, r) });
  const noVersions = versions.error instanceof ApiError && versions.error.status === 404;

  const patchRow = (fresh: WorkbenchRow) => {
    qc.setQueryData(["formulation", "campaign", c], (old: typeof campaign.data) => old && { ...old, rows: old.rows.map((x) => (x.id === fresh.id ? fresh : x)) });
    message.success(t("common.saved", "已保存"));
  };
  const saveNote = useMutation({ mutationFn: (note: string) => fx.setRowNote(c, r, note || null), onSuccess: patchRow, onError: (e) => message.error(errorText(e)) });
  const saveTags = useMutation({ mutationFn: (tags: string[]) => fx.setRowTags(c, r, tags), onSuccess: patchRow, onError: (e) => message.error(errorText(e)) });

  if (campaign.isSuccess && !row) return <Result status="404" title={t("formulation.row.not_found", "台账行不存在")} />;

  return (
    <PageContainer
      loading={campaign.isLoading}
      title={t("formulation.row.title", "行 #{{id}}", { id: rowId })}
      subTitle={campaign.data?.name}
      tags={row ? <Space size={4}>
        <StatusTag status={row.status} />
        {row.ingested && <Tag color="success">{t("formulation.row.ingested", "已入训练")}</Tag>}
        {row.refcode && <Tag>{row.refcode}</Tag>}
      </Space> : undefined}
      extra={row && <Space wrap>
        <Button icon={<EditOutlined />} onClick={() => setNoteOpen(true)}>{t("formulation.row.note", "备注")}</Button>
        <Button icon={<TagsOutlined />} onClick={() => setTagOpen(true)}>{t("formulation.row.tags", "标签")}</Button>
        <Select style={{ width: 200 }} placeholder={t("formulation.row.compare_with", "与另一行对比…")} value={null}
          options={(campaign.data?.rows ?? []).filter((x) => x.id !== rowId).map((x) => ({ value: x.id, label: `#${x.id} ${x.item_id || ""}` }))}
          onChange={(b: number) => navigate(`/lab/experiments/workbench/${c}/diff?a=${rowId}&b=${b}`)} />
      </Space>}
      tabActiveKey={tab}
      onTabChange={(k) => setParams(k === "overview" ? {} : { tab: k })}
      tabList={[
        { key: "overview", tab: t("formulation.row.overview", "详情") },
        { key: "attachments", tab: <Space size={4}>{t("formulation.row.attachments", "附件")}<Badge count={atts.data?.length ?? 0} showZero color={token.colorTextQuaternary} /></Space> },
        { key: "versions", tab: <Space size={4}>{t("formulation.row.versions", "版本")}{versions.data && <Badge count={versions.data.versions.length} showZero color={token.colorTextQuaternary} />}</Space> },
        { key: "lineage", tab: t("formulation.row.lineage", "谱系") },
      ]}
    >
      <ErrorAlert error={campaign.error} />
      {row && tab === "overview" && <Overview row={row} objectives={objectives} chain={lineage.data} meas={meas.data} measLoading={meas.isLoading} />}
      {row && tab === "attachments" && <ProCard bordered><RowAttachments campaignId={campaignId} rowId={rowId} /></ProCard>}
      {row && tab === "versions" && <ProCard bordered>
        {!noVersions ? <RowVersions campaignId={campaignId} rowId={rowId} onRestored={() => void qc.invalidateQueries({ queryKey: ["formulation", "campaign", c] })} />
          : <Empty description={t("formulation.rowver.no_refcode", "该行尚未同步到 DataLab，无版本记录")} />}
      </ProCard>}
      {row && tab === "lineage" && <ProCard bordered><RowLineage campaignId={campaignId} rowId={rowId} chain={lineage.data} loading={lineage.isLoading} error={lineage.error} /></ProCard>}
      {row && <>
        <NoteModal open={noteOpen} initial={row.note ?? ""} onClose={() => setNoteOpen(false)} onSave={(n) => saveNote.mutateAsync(n)} />
        <TagModal open={tagOpen} initial={row.tags ?? []} onClose={() => setTagOpen(false)} onSave={(tags) => saveTags.mutateAsync(tags)} />
      </>}
    </PageContainer>
  );
}

function Overview({ row, objectives, chain, meas, measLoading }: {
  row: WorkbenchRow; objectives: ObjectiveSpec[]; chain?: WorkbenchRow[]; meas?: Awaited<ReturnType<typeof fx.rowMeasurements>>; measLoading: boolean;
}) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const pva = planVsActual(row);
  const verdicts = objectives.map((o) => ({ o, value: row.measurements?.[o.metric], pass: valueMeetsSpec(o, row.measurements?.[o.metric]) })).filter((x) => x.pass !== null);
  const trend = lineageTrend(chain ?? [], objectives.map((o) => o.metric));
  const label = (m: string) => objectives.find((o) => o.metric === m)?.display_name || m;
  const option: EChartsOption = {
    tooltip: { trigger: "axis" }, legend: { top: 0 }, grid: { left: 40, right: 16, top: 32, bottom: 28 },
    xAxis: { type: "category", data: trend.rounds, name: t("formulation.row.round", "代") },
    yAxis: { type: "value", scale: true },
    series: trend.series.map((s) => ({ type: "line", name: label(s.metric), data: s.data, connectNulls: true, symbolSize: 6 })),
  };
  const reports = (meas?.attachments ?? []).filter((a) => a.kind === "qc_report");
  return (
    <ProCard direction="column" gutter={[0, 16]} ghost>
      <ProCard gutter={16} ghost wrap>
        <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("formulation.row.plan_actual", "计划 vs 实际")} subTitle={t("formulation.row.plan_hint", "高亮 = 已偏离")}>
          <Table size="small" pagination={false} rowKey="key" dataSource={pva} locale={{ emptyText: <Empty /> }}
            onRow={(x) => ({ style: x.deviated ? { background: token.colorWarningBg } : undefined })}
            columns={[
              { title: t("formulation.row.factor", "因子"), dataIndex: "key" },
              { title: t("formulation.row.planned", "计划"), dataIndex: "planned", render: fmtValue },
              { title: t("formulation.row.actual", "实际"), dataIndex: "actual", render: (v: unknown, x) => <Typography.Text strong={x.deviated} type={x.deviated ? "warning" : undefined}>{fmtValue(v)}</Typography.Text> },
            ]} />
        </ProCard>
        <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("formulation.row.spec", "测量 vs 规格")}>
          {verdicts.length === 0 ? <Empty description={t("formulation.row.no_spec", "暂无可判定的实测值")} /> : (
            <Table size="small" pagination={false} rowKey={(x) => x.o.metric} dataSource={verdicts} columns={[
              { title: t("formulation.exp.metric", "指标"), render: (_d, x) => x.o.display_name || x.o.metric },
              { title: t("formulation.exp.value", "数值"), render: (_d, x) => fmtValue(x.value) },
              { title: t("formulation.exp.spec", "规格"), render: (_d, x) => specText(x.o) || "—" },
              { title: t("formulation.exp.passed", "判定"), render: (_d, x) => x.pass ? <Tag color="success">{t("formulation.row.pass", "达标")}</Tag> : <Tag color="error">{t("formulation.row.fail", "未达标")}</Tag> },
            ]} />
          )}
          <Descriptions size="small" column={1} style={{ marginTop: 12 }} items={[
            { key: "tags", label: t("formulation.row.tags", "标签"), children: <TagList tags={row.tags} /> },
            { key: "note", label: t("formulation.row.note", "备注"), children: row.note || "—" },
            { key: "parent", label: t("formulation.row.parent", "父样品"), children: row.parent_sample_id ? `${row.parent_sample_id}${row.parent_campaign_id ? ` · C${row.parent_campaign_id}` : ""}` : "—" },
          ]} />
        </ProCard>
      </ProCard>
      <ProCard bordered title={t("formulation.row.trend", "指标趋势（沿谱系链）")}>
        {trend.rounds.length > 1 && trend.series.length ? <Chart option={option} height={260} />
          : <Empty description={t("formulation.row.no_trend", "暂无多轮趋势（随闭环迭代沿谱系自动生成）")} />}
      </ProCard>
      <ProCard bordered title={t("formulation.row.qc", "检测报告（{{n}}）", { n: reports.length })}
        extra={<Link to={`/lab/qc-report?campaign=${row.campaign_id}&row=${row.id}`}>{t("formulation.row.qc_upload", "上传检测报告")}</Link>}>
        {reports.length > 0 && <Space wrap style={{ marginBottom: 12 }}>{reports.map((a) => <Tag key={a.id}>{a.source_document_id.slice(0, 16)}</Tag>)}</Space>}
        <MeasurementTable rows={meas?.measurements ?? []} loading={measLoading} />
      </ProCard>
    </ProCard>
  );
}
