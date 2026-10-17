import { App, Button, Descriptions, Drawer, Empty, Input, List, Popconfirm, Select, Space, Table, Tabs, Tag, Typography, Upload } from "antd";
import { DeleteOutlined, DownloadOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { specText, valueMeetsSpec } from "./ledger";
import type { Attachment, ObjectiveSpec, QcMeasurement, VersionEntry, WorkbenchRow } from "./types";

const base = (cid: number, rid: number) => `/api/experiments/workbench/${cid}/rows/${rid}`;

function DetailTab({ row, objectives, factors }: { row: WorkbenchRow; objectives: ObjectiveSpec[]; factors: string[] }) {
  const { t } = useTranslation();
  return (
    <>
      <Descriptions size="small" column={2} bordered items={[
        { key: "status", label: t("workbench.col.status", "状态"), children: row.status },
        { key: "item", label: "item_id", children: row.item_id ?? "—" },
        { key: "refcode", label: "refcode", children: row.refcode ?? "—" },
        { key: "ingested", label: t("workbench.ingested", "已入训练"), children: row.ingested ? <Tag color="green">✓</Tag> : "—" },
        ...(row.parent_sample_id ? [{ key: "parent", label: t("workbench.parent", "父样品"), children: `${row.parent_campaign_id ?? ""}:${row.parent_sample_id}` }] : []),
      ]} />
      <Table size="small" style={{ marginTop: 12 }} rowKey="k" pagination={false}
        dataSource={factors.map((k) => ({ k, planned: row.planned_params?.[k], actual: row.actual_params?.[k] }))}
        columns={[
          { title: t("workbench.factor", "因子"), dataIndex: "k" },
          { title: t("workbench.group.planned", "DOE 计划"), dataIndex: "planned" },
          { title: t("workbench.group.actual", "实验实际值"), dataIndex: "actual", render: (v) => v ?? "—" },
        ]} />
      <Table size="small" style={{ marginTop: 12 }} rowKey="metric" pagination={false} dataSource={objectives}
        columns={[
          { title: t("workbench.metric", "指标"), render: (_v, o) => o.display_name || o.metric },
          { title: t("workbench.spec", "规格"), render: (_v, o) => specText(o) || "—" },
          { title: t("workbench.measured", "实测"), render: (_v, o) => {
            const v = row.measurements?.[o.metric];
            const ok = valueMeetsSpec(o, v);
            return v == null || v === "" ? <Typography.Text type="secondary">{t("workbench.pending_fill", "待回填")}</Typography.Text>
              : <Tag color={ok === false ? "error" : ok ? "success" : "default"}>{String(v)}</Tag>;
          } },
          { title: t("workbench.predicted", "预测"), render: (_v, o) => (row.predicted?.[o.metric] != null ? String(row.predicted[o.metric]) : "—") },
        ]} />
    </>
  );
}

function NoteTagsTab({ campaignId, row, onSaved }: { campaignId: number; row: WorkbenchRow; onSaved: (r: WorkbenchRow) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [note, setNote] = useState(row.note ?? "");
  const [tags, setTags] = useState<string[]>(row.tags ?? []);
  useEffect(() => { setNote(row.note ?? ""); setTags(row.tags ?? []); }, [row.id, row.note, row.tags]);
  const saveNote = useMutation({ mutationFn: () => call<WorkbenchRow>("PUT", `${base(campaignId, row.id)}/note`, { note: note || null }),
    onSuccess: (r) => { onSaved(r); message.success(t("workbench.note_saved", "备注已保存")); } });
  const saveTags = useMutation({ mutationFn: () => call<WorkbenchRow>("PUT", `${base(campaignId, row.id)}/tags`, { tags }),
    onSuccess: (r) => { onSaved(r); message.success(t("workbench.tags_saved", "标签已保存")); } });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <ErrorAlert error={saveNote.error ?? saveTags.error} />
      <Typography.Text strong>{t("workbench.note", "备注")}</Typography.Text>
      <Input.TextArea rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
      <Button type="primary" loading={saveNote.isPending} onClick={() => saveNote.mutate()}>{t("workbench.save_note", "保存备注")}</Button>
      <Typography.Text strong style={{ marginTop: 12 }}>{t("workbench.tags", "标签")}</Typography.Text>
      <Select mode="tags" value={tags} onChange={setTags} style={{ width: "100%" }} placeholder={t("workbench.tags_ph", "输入后回车添加，如 复测 / 异常 / 优选")}
        options={["复测", "异常", "优选", "对照", "待确认"].map((v) => ({ value: v }))} />
      <Button type="primary" loading={saveTags.isPending} onClick={() => saveTags.mutate()}>{t("workbench.save_tags", "保存标签")}</Button>
    </Space>
  );
}

function AttachmentsTab({ campaignId, rowId, onCount }: { campaignId: number; rowId: number; onCount?: (n: number) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [kind, setKind] = useState("report");
  const key = ["workbench", campaignId, "row", rowId, "attachments"];
  const q = useQuery({ queryKey: key, queryFn: () => call<Attachment[]>("GET", `${base(campaignId, rowId)}/attachments`) });
  useEffect(() => { if (q.data) onCount?.(q.data.length); }, [q.data, onCount]);
  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file);
      return call<Attachment>("POST", `${base(campaignId, rowId)}/attachments?kind=${encodeURIComponent(kind)}`, fd); },
    onSuccess: () => { message.success(t("workbench.uploaded", "已上传")); void qc.invalidateQueries({ queryKey: key }); },
  });
  const del = useMutation({ mutationFn: (id: string) => call("DELETE", `${base(campaignId, rowId)}/attachments/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }) });
  return (
    <>
      <ErrorAlert error={q.error ?? upload.error ?? del.error} />
      <Space style={{ marginBottom: 12 }}>
        <Select value={kind} onChange={setKind} style={{ width: 140 }} options={[
          { value: "report", label: t("workbench.att.report", "检测报告") }, { value: "photo", label: t("workbench.att.photo", "照片") },
          { value: "raw_data", label: t("workbench.att.raw", "原始数据") }, { value: "other", label: t("workbench.att.other", "其他") }]} />
        <Upload showUploadList={false} beforeUpload={(f) => { upload.mutate(f); return false; }}>
          <Button icon={<UploadOutlined />} loading={upload.isPending}>{t("workbench.upload", "上传附件")}</Button>
        </Upload>
      </Space>
      <List loading={q.isLoading} dataSource={q.data ?? []} rowKey="id" locale={{ emptyText: t("workbench.att.empty", "暂无附件") }}
        renderItem={(a) => (
          <List.Item actions={[
            <Button key="dl" size="small" icon={<DownloadOutlined />} onClick={() => void download(`${base(campaignId, rowId)}/attachments/${a.id}/download`, a.filename)} />,
            <Popconfirm key="del" title={t("workbench.att.confirm_delete", "删除该附件？")} onConfirm={() => del.mutate(a.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>,
          ]}>
            <List.Item.Meta title={a.filename} description={<Space><Tag>{a.kind}</Tag>{a.created_at?.slice(0, 16).replace("T", " ")}{a.note}</Space>} />
          </List.Item>
        )} />
    </>
  );
}

function QcTab({ campaignId, rowId, onSynced }: { campaignId: number; rowId: number; onSynced: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const key = ["workbench", campaignId, "row", rowId, "qc"];
  const q = useQuery({ queryKey: key, retry: false,
    queryFn: () => call<{ measurements: QcMeasurement[]; attachments: { id: string; kind: string }[] }>("GET", `/api/qc/workbench/${campaignId}/rows/${rowId}/measurements`) });
  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); fd.append("campaign_id", String(campaignId));
      fd.append("row_id", String(rowId)); fd.append("sync_measured", "true");
      return call<{ measurement_count: number }>("POST", "/api/qc/report", fd); },
    onSuccess: (r) => { message.success(t("workbench.qc.parsed", "已解析 {{n}} 项测量并回填", { n: r.measurement_count }));
      void qc.invalidateQueries({ queryKey: key }); onSynced(); },
  });
  return (
    <>
      <Typography.Paragraph type="secondary">{t("workbench.qc.hint", "上传检测报告（PDF / Excel / 图片），自动抽取测量值并回填到该行。")}</Typography.Paragraph>
      <Upload showUploadList={false} beforeUpload={(f) => { upload.mutate(f); return false; }}>
        <Button icon={<UploadOutlined />} loading={upload.isPending}>{t("workbench.qc.upload", "上传检测报告")}</Button>
      </Upload>
      <ErrorAlert error={upload.error} />
      <Table size="small" style={{ marginTop: 12 }} rowKey={(m) => `${m.metric}-${m.test_method}`} loading={q.isLoading} pagination={false}
        dataSource={q.data?.measurements ?? []} locale={{ emptyText: t("workbench.qc.empty", "暂无检测测量") }}
        columns={[
          { title: t("workbench.metric", "指标"), dataIndex: "metric" },
          { title: t("workbench.value", "值"), render: (_v, m) => `${m.value} ${m.unit ?? ""}` },
          { title: t("workbench.qc.method", "方法"), dataIndex: "test_method" },
          { title: t("workbench.spec", "规格"), render: (_v, m) => [m.spec_min, m.spec_max].map((x) => x ?? "").join(" – ") },
          { title: t("workbench.qc.result", "判定"), dataIndex: "passed", render: (p: boolean | null) => (p == null ? "—" : <Tag color={p ? "success" : "error"}>{p ? "PASS" : "FAIL"}</Tag>) },
        ]} />
    </>
  );
}

function VersionsTab({ campaignId, row, onRestored }: { campaignId: number; row: WorkbenchRow; onRestored: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const key = ["workbench", campaignId, "row", row.id, "versions"];
  const q = useQuery({ queryKey: key, enabled: !!row.refcode, retry: false,
    queryFn: () => call<{ refcode: string; versions: VersionEntry[] }>("GET", `${base(campaignId, row.id)}/versions`) });
  const [diff, setDiff] = useState<{ v1: number; v2: number; keys: string[] } | null>(null);
  const restore = useMutation({ mutationFn: (v: VersionEntry) => call("POST", `${base(campaignId, row.id)}/versions/${v.id}/restore`, {}),
    onSuccess: () => { message.success(t("workbench.ver.restored", "已恢复（平台已留新版本，可再次恢复）")); void qc.invalidateQueries({ queryKey: key }); onRestored(); } });
  const compare = useMutation({
    mutationFn: async (v: VersionEntry) => {
      const vs = q.data?.versions ?? [];
      const prev = vs.find((x) => x.version === v.version - 1) ?? vs.find((x) => x.id !== v.id);
      if (!prev) throw new Error(t("workbench.ver.none", "没有可对比的版本"));
      const r = await call<{ diff: Record<string, unknown> }>("GET", `${base(campaignId, row.id)}/versions?compare_v1=${prev.id}&compare_v2=${v.id}`);
      return { v1: prev.version, v2: v.version, keys: Object.keys(r.diff ?? {}) };
    },
    onSuccess: setDiff,
  });
  if (!row.refcode) return <Empty description={t("workbench.ver.no_refcode", "该行没有 ELN 版本记录（本地台账不保存逐次版本）")} />;
  return (
    <>
      <ErrorAlert error={q.error ?? restore.error ?? compare.error} />
      {diff && <Typography.Paragraph>{t("workbench.ver.diff", "v{{a}} → v{{b}} 变更：{{k}}", { a: diff.v1, b: diff.v2, k: diff.keys.join(", ") || t("workbench.ver.same", "两个版本内容一致") })}</Typography.Paragraph>}
      <Table size="small" rowKey="id" loading={q.isLoading} pagination={false} dataSource={q.data?.versions ?? []}
        columns={[
          { title: "v", dataIndex: "version", width: 50 },
          { title: t("workbench.ver.time", "时间"), dataIndex: "timestamp", render: (v?: string) => v?.replace("T", " ").slice(0, 16) ?? "—" },
          { title: t("workbench.ver.action", "操作"), dataIndex: "action" },
          { title: t("workbench.ver.creator", "操作人"), dataIndex: "creator" },
          { title: "", render: (_v, v: VersionEntry) => (
            <Space>
              <Button size="small" loading={compare.isPending && compare.variables?.id === v.id} onClick={() => compare.mutate(v)}>{t("workbench.ver.compare", "对比上一版")}</Button>
              <Popconfirm title={t("workbench.ver.confirm", "恢复到 v{{v}}？将覆盖该行当前的参数/测量/状态（可逆）。", { v: v.version })} onConfirm={() => restore.mutate(v)}>
                <Button size="small">{t("workbench.ver.restore", "恢复")}</Button>
              </Popconfirm>
            </Space>
          ) },
        ]} />
    </>
  );
}

function LineageTab({ campaignId, rowId }: { campaignId: number; rowId: number }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["workbench", campaignId, "row", rowId, "lineage"], queryFn: () => call<WorkbenchRow[]>("GET", `${base(campaignId, rowId)}/lineage`) });
  return (
    <>
      <ErrorAlert error={q.error} />
      <Table size="small" rowKey={(r) => `${r.campaign_id}:${r.id}`} loading={q.isLoading} pagination={false} dataSource={q.data ?? []}
        columns={[
          { title: t("workbench.lineage.gen", "代"), render: (_v, _r, i) => (i === 0 ? t("workbench.lineage.self", "本行") : t("workbench.lineage.parent_n", "上 {{n}} 代", { n: i })) },
          { title: "Campaign", dataIndex: "campaign_id" },
          { title: t("workbench.col.row", "行"), dataIndex: "id" },
          { title: t("workbench.col.status", "状态"), dataIndex: "status" },
          { title: t("workbench.measured", "实测"), render: (_v, r) => Object.entries(r.measurements ?? {}).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`).join("; ") || "—" },
        ]} />
    </>
  );
}

/** Row detail drawer — replaces legacy RowDetailBar / ExperimentDetail / NoteEditor / TagPicker / AttachmentPreview / QCReportModal / RowVersionHistoryModal / LineageTree. */
export function RowDrawer({ campaignId, row, objectives, factors, tab, onTab, onClose, onRowSaved, onReload, onAttachmentCount }: {
  campaignId: number; row: WorkbenchRow | null; objectives: ObjectiveSpec[]; factors: string[]; tab: string; onTab: (k: string) => void;
  onClose: () => void; onRowSaved: (r: WorkbenchRow) => void; onReload: () => void; onAttachmentCount?: (rowId: number, n: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <Drawer width={720} open={!!row} onClose={onClose} destroyOnHidden
      title={row ? t("workbench.row_title", "实验行 #{{id}}", { id: row.id }) : ""}>
      {row && (
        <Tabs activeKey={tab} onChange={onTab} items={[
          { key: "detail", label: t("workbench.tab.detail", "详情"), children: <DetailTab row={row} objectives={objectives} factors={factors} /> },
          { key: "note", label: t("workbench.tab.note", "备注与标签"), children: <NoteTagsTab campaignId={campaignId} row={row} onSaved={onRowSaved} /> },
          { key: "attachments", label: t("workbench.tab.attachments", "附件"),
            children: <AttachmentsTab campaignId={campaignId} rowId={row.id} onCount={(n) => onAttachmentCount?.(row.id, n)} /> },
          { key: "qc", label: t("workbench.tab.qc", "检测报告"), children: <QcTab campaignId={campaignId} rowId={row.id} onSynced={onReload} /> },
          { key: "versions", label: t("workbench.tab.versions", "版本历史"), children: <VersionsTab campaignId={campaignId} row={row} onRestored={onReload} /> },
          { key: "lineage", label: t("workbench.tab.lineage", "谱系"), children: <LineageTab campaignId={campaignId} rowId={row.id} /> },
        ]} />
      )}
    </Drawer>
  );
}
