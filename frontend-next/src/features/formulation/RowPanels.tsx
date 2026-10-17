/** Panels of one workbench row: attachments, DataLab versions, lineage chain, note and tag editors. */
import { ModalForm, ProFormTextArea } from "@ant-design/pro-components";
import { Alert, App, Button, Empty, Input, Modal, Popconfirm, Space, Steps, Table, Tag, Typography, Upload } from "antd";
import { DeleteOutlined, DownloadOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { download } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import { fx } from "./api";
import { deepDiffSummary, rowFactors } from "./logic";
import type { Attachment, RowVersion, WorkbenchRow } from "./types";

export const rowKey = (c: number | string, r: number | string) => ["formulation", "row", String(c), String(r)];

/** Attachment list + upload + unbind for one row (legacy AttachmentPreview). */
export function RowAttachments({ campaignId, rowId }: { campaignId: number; rowId: number }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const key = [...rowKey(campaignId, rowId), "attachments"];
  const list = useQuery({ queryKey: key, queryFn: () => fx.rowAttachments(campaignId, rowId) });
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const upload = useMutation({
    mutationFn: (f: File) => fx.uploadRowAttachment(campaignId, rowId, f),
    onSuccess: () => { message.success(t("formulation.att.uploaded", "已上传")); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (a: Attachment) => fx.deleteRowAttachment(campaignId, rowId, a.id),
    onSuccess: refresh,
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Upload showUploadList={false} beforeUpload={(f) => { upload.mutate(f); return false; }}>
          <Button icon={<UploadOutlined />} loading={upload.isPending}>{t("formulation.att.upload", "上传")}</Button>
        </Upload>
        <Typography.Text type="secondary">{t("formulation.att.bind_hint", "文件绑定到该行回灌的训练实验（需先保存台账为 Completed 且有实测值）")}</Typography.Text>
      </Space>
      <ErrorAlert error={list.error} />
      <Table<Attachment> size="small" rowKey="id" pagination={false} loading={list.isLoading} dataSource={list.data}
        locale={{ emptyText: <Empty description={t("formulation.att.empty", "暂无附件")} /> }}
        columns={[
          { title: t("formulation.att.file", "文件"), render: (_d, a) => a.filename || a.source_document_id },
          { title: t("formulation.att.kind", "类型"), dataIndex: "kind", width: 110, render: (k: string) => <Tag>{k}</Tag> },
          { title: t("common.actions", "操作"), width: 150, render: (_d, a) => <Space>
            <Button size="small" type="link" icon={<DownloadOutlined />} title={t("formulation.att.download_hint", "下载原件（DataLab 归档副本优先）")}
              onClick={() => download(fx.rowAttachmentDownload(campaignId, rowId, a.id), a.filename || a.source_document_id).catch((e) => message.error(errorText(e)))}>
              {t("formulation.att.download", "下载")}</Button>
            <Popconfirm title={t("formulation.att.delete_q", "删除附件「{{f}}」？", { f: a.filename || a.source_document_id })}
              description={t("formulation.att.delete_desc", "仅解除与实验的绑定，DataLab 归档副本保留（可追溯）。")}
              onConfirm={() => remove.mutate(a)}>
              <Button size="small" type="link" danger icon={<DeleteOutlined />} loading={remove.isPending && remove.variables?.id === a.id} />
            </Popconfirm>
          </Space> },
        ]} />
    </>
  );
}

/** DataLab auto-saved versions of a row: compare with the previous one, restore (legacy RowVersionHistoryModal). */
export function RowVersions({ campaignId, rowId, onRestored }: { campaignId: number; rowId: number; onRestored?: () => void }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const qc = useQueryClient();
  const key = [...rowKey(campaignId, rowId), "versions"];
  const list = useQuery({ queryKey: key, queryFn: () => fx.rowVersions(campaignId, rowId), retry: false });
  const versions = list.data?.versions ?? [];
  const [diff, setDiff] = useState<{ v1: number; v2: number; lines: string[] } | null>(null);
  const compare = useMutation({
    mutationFn: async (v: RowVersion) => {
      const target = versions.find((x) => x.version === v.version - 1) ?? versions.find((x) => x.id !== v.id);
      if (!target) throw new Error(t("formulation.rowver.none", "没有可对比的版本"));
      const res = await fx.compareRowVersions(campaignId, rowId, target.id, v.id);
      return { v1: target.version, v2: v.version, lines: deepDiffSummary(res.diff) };
    },
    onSuccess: setDiff,
    onError: (e) => message.error(errorText(e)),
  });
  const restore = useMutation({
    mutationFn: (v: RowVersion) => fx.restoreRowVersion(campaignId, rowId, v.id),
    onSuccess: () => { message.success(t("formulation.rowver.restored", "已恢复")); void qc.invalidateQueries({ queryKey: rowKey(campaignId, rowId) }); onRestored?.(); },
    onError: (e) => message.error(errorText(e)),
  });
  const askRestore = (v: RowVersion) => modal.confirm({
    title: t("formulation.rowver.restore_q", "将行 #{{row}} 恢复到 v{{v}}？", { row: rowId, v: v.version }),
    content: t("formulation.rowver.restore_desc", "将覆盖该行当前的参数 / 测量 / 状态。平台会立即留一个新版本，可再次恢复——此操作可逆。"),
    onOk: () => restore.mutateAsync(v),
  });
  return (
    <>
      <Typography.Paragraph type="secondary">
        {t("formulation.rowver.intro", "每次保存台账时 DataLab 自动留档（零成本审计轨迹）。")}{list.data?.refcode && <Tag style={{ marginLeft: 8 }}>{list.data.refcode}</Tag>}
      </Typography.Paragraph>
      <ErrorAlert error={list.error} />
      <Table<RowVersion> size="small" rowKey="id" pagination={false} loading={list.isLoading} dataSource={versions}
        locale={{ emptyText: <Empty description={t("formulation.rowver.empty", "暂无版本记录")} /> }}
        columns={[
          { title: t("formulation.ver.version", "版本"), dataIndex: "version", width: 70, render: (v: number) => <b>v{v}</b> },
          { title: t("formulation.rowver.action", "动作"), dataIndex: "action", width: 110 },
          { title: t("formulation.rowver.time", "时间"), dataIndex: "timestamp", render: (v?: string) => v?.replace("T", " ").slice(0, 19) ?? "—" },
          { title: t("formulation.ver.author", "操作人"), dataIndex: "creator", render: (v?: string | null) => v || "—" },
          { title: t("common.actions", "操作"), width: 160, render: (_d, v) => <Space>
            <Button size="small" type="link" disabled={versions.length < 2} loading={compare.isPending && compare.variables?.id === v.id}
              onClick={() => compare.mutate(v)}>{t("formulation.ver.compare", "对比")}</Button>
            <Button size="small" type="link" danger disabled={restore.isPending} onClick={() => askRestore(v)}>{t("formulation.rowver.restore", "恢复")}</Button>
          </Space> },
        ]} />
      {diff && <Alert style={{ marginTop: 12 }} type="info" closable onClose={() => setDiff(null)}
        message={t("formulation.rowver.diff", "v{{a}} → v{{b}} 差异", { a: diff.v1, b: diff.v2 })}
        description={diff.lines.length ? <ul style={{ margin: 0, paddingInlineStart: 18 }}>{diff.lines.map((l) => <li key={l}><code>{l}</code></li>)}</ul>
          : t("formulation.rowver.same", "两个版本内容一致")} />}
    </>
  );
}

/** Formula lineage chain root → current (legacy LineageTree). */
export function RowLineage({ campaignId, rowId, chain, loading, error }: { campaignId: number; rowId: number; chain: WorkbenchRow[] | undefined; loading: boolean; error: unknown }) {
  const { t } = useTranslation();
  const rows = [...(chain ?? [])].reverse();
  const keys = Array.from(new Set(rows.flatMap((r) => Object.keys(rowFactors(r))))).slice(0, 8);
  if (error) return <ErrorAlert error={error} />;
  if (!loading && !rows.length) return <Empty description={t("formulation.lineage.none", "无谱系信息")} />;
  return (
    <Steps direction="vertical" size="small" current={rows.length - 1} items={rows.map((r, i) => {
      const f = rowFactors(r);
      return {
        key: `${r.campaign_id}:${r.id}`,
        title: <Space>
          {r.id === rowId && r.campaign_id === campaignId
            ? t("formulation.lineage.gen", "第 {{g}} 代 · 行 #{{id}}", { g: i + 1, id: r.id })
            : <Link to={`/lab/experiments/workbench/${r.campaign_id}/rows/${r.id}`}>{t("formulation.lineage.gen", "第 {{g}} 代 · 行 #{{id}}", { g: i + 1, id: r.id })}</Link>}
          {r.campaign_id !== campaignId && <Tag>C{r.campaign_id}</Tag>}
          <StatusTag status={r.status} />
        </Space>,
        description: <Space direction="vertical" size={4}>
          <Space wrap size={4}>{keys.filter((k) => f[k] !== undefined).map((k) => <Tag key={k}>{k}: {typeof f[k] === "number" ? (f[k] as number).toFixed(2) : String(f[k])}</Tag>)}</Space>
          <Space wrap size={4}>{Object.entries(r.measurements || {}).map(([k, v]) => <Tag key={k} color="cyan">{k}: {String(v)}</Tag>)}</Space>
        </Space>,
      };
    })} />
  );
}

/** Experiment note editor (legacy NoteEditor). */
export function NoteModal({ open, initial, onClose, onSave }: { open: boolean; initial: string; onClose: () => void; onSave: (note: string) => Promise<unknown> }) {
  const { t } = useTranslation();
  return (
    <ModalForm<{ note: string }> title={t("formulation.note.title", "实验备注")} open={open} initialValues={{ note: initial }}
      modalProps={{ destroyOnHidden: true, onCancel: onClose }} width={480}
      onFinish={async (v) => { await onSave(v.note ?? ""); onClose(); return true; }}>
      <ProFormTextArea name="note" fieldProps={{ rows: 6 }} placeholder={t("formulation.note.ph", "记录操作偏差、异常观察、参数调整原因…")} />
    </ModalForm>
  );
}

export const PRESET_TAGS = ["验证批次", "客户样件", "待复现", "失败", "最佳候选", "预混料异常", "固化异常"];

/** Tag picker: preset tags + custom (legacy TagPicker). */
export function TagModal({ open, initial, onClose, onSave }: { open: boolean; initial: string[]; onClose: () => void; onSave: (tags: string[]) => Promise<unknown> }) {
  const { t } = useTranslation();
  const [sel, setSel] = useState<string[]>(initial);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const all = Array.from(new Set([...PRESET_TAGS, ...sel]));
  const add = () => {
    const v = custom.trim();
    if (v && !sel.includes(v)) setSel([...sel, v]);
    setCustom("");
  };
  return (
    <Modal open={open} title={t("formulation.tags.title", "标记标签")} onCancel={onClose} confirmLoading={busy} destroyOnHidden
      afterOpenChange={(o) => { if (o) setSel(initial); }}
      onOk={async () => { setBusy(true); try { await onSave(sel); onClose(); } finally { setBusy(false); } }}>
      <Space wrap style={{ marginBottom: 12 }}>
        {all.map((tag) => <Tag.CheckableTag key={tag} checked={sel.includes(tag)}
          onChange={(c) => setSel(c ? [...sel, tag] : sel.filter((x) => x !== tag))}>{tag}</Tag.CheckableTag>)}
      </Space>
      <Space.Compact style={{ width: "100%" }}>
        <Input value={custom} onChange={(e) => setCustom(e.target.value)} onPressEnter={add} placeholder={t("formulation.tags.custom", "自定义标签…")} />
        <Button onClick={add}>{t("formulation.tags.add", "添加")}</Button>
      </Space.Compact>
    </Modal>
  );
}

export function TagList({ tags }: { tags?: string[] }) {
  if (!tags?.length) return <Typography.Text type="secondary">—</Typography.Text>;
  return <Space wrap size={4}>{tags.map((x) => <Tag key={x} color="purple">{x}</Tag>)}</Space>;
}
