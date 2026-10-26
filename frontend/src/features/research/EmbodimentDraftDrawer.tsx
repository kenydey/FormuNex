import { Alert, App, Button, Descriptions, Drawer, List, Segmented, Space, Table, Tag, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { confirmPath, draftForEmbodiment } from "./lib";
import type { DraftConfirmResult, EmbodimentDraft, FormulationIngredient } from "./types";

/**
 * Human review gate for an embodiment / SureChEMBL example draft (P3.1). Confirming writes the KG and *pending*
 * raw materials only — never the production formulation pool.
 */
export function EmbodimentDraftDrawer({ draft, onClose, onConfirmed }: {
  draft: EmbodimentDraft | null; onClose: () => void; onConfirmed?: (note: string) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const [idx, setIdx] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const [key, setKey] = useState<EmbodimentDraft | null>(draft);
  if (key !== draft) {
    // A different draft was opened: reset the per-draft state.
    setKey(draft);
    setIdx(0);
    setNote(null);
  }
  const active = useMemo(() => (draft ? draftForEmbodiment(draft, idx) : null), [draft, idx]);
  const confirm = useMutation({
    mutationFn: async (d: EmbodimentDraft) => {
      const res = await call<DraftConfirmResult>("POST", confirmPath(d.origin), { draft: d });
      if (!res.ok) throw new Error(t("research.emb.confirm_failed", "确认失败"));
      return res;
    },
    onSuccess: (res) => {
      if (res.promoted_to_pool) {
        message.error(t("research.emb.gate_broken", "安全闸失败：不应写入生产配方池"));
        return;
      }
      const pending = (res.pending_materials ?? []).filter((p) => p.action === "pending").length;
      const text = res.note || t("research.emb.confirmed", "已确认：{{n}} 条原料进入 pending；配方实体 {{id}}；未写入生产配方池",
        { n: pending, id: res.formulation_entity_id });
      setNote(text);
      message.success(text);
      onConfirmed?.(text);
    },
    onError: (e) => message.error(errorText(e)),
  });
  if (!draft || !active) return <Drawer open={false} onClose={onClose} />;
  const ingredients = active.formulation?.ingredients ?? [];
  const amountSource = active.amount_source || "placeholder";
  const placeholder = amountSource === "placeholder";
  const has = ingredients.length > 0;
  const embodiments = draft.embodiments ?? [];
  return (
    <Drawer open width={720} onClose={onClose} destroyOnHidden
      title={t("research.emb.title", "实施例草稿 · {{id}}", { id: draft.doc_id || draft.source_id || "" })}
      extra={<Button type="primary" disabled={!has || !!note || !can("formulation.write")} loading={confirm.isPending} onClick={() => confirm.mutate(active)}>
        {t("research.emb.confirm", "人工确认入库")}</Button>}>
      <Alert type="warning" showIcon style={{ marginBottom: 16 }}
        message={t("research.emb.gate", "人审闸：确认后仅写入 KG + 原料 pending，不会写入生产配方池或静默 upsert 原料。")}
        description={placeholder
          ? has ? t("research.emb.placeholder", "当前比重为占位均分，非原文真实配比。")
            : t("research.emb.no_table", "未识别到可用配方表或组分——请改用含表格的 PDF 再入库全文，或人工填写后再确认。")
          : t("research.emb.from_table", "比重来自已入库全文表格（已归一），请核对原件。")} />
      {embodiments.length > 1 && (
        <Segmented style={{ marginBottom: 12 }} value={idx} onChange={(v) => setIdx(Number(v))}
          options={embodiments.map((e, i) => ({ value: i, label: e.label || `Example ${i + 1}` }))} />
      )}
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }} items={[
        { key: "title", label: t("research.emb.doc_title", "标题"), span: 2, children: draft.title || draft.doc_id },
        ...(draft.source_id ? [{ key: "sid", label: "source_id", span: 2, children: <Typography.Text code>{draft.source_id}</Typography.Text> }] : []),
        ...(draft.assignee ? [{ key: "as", label: t("research.emb.assignee", "专利权人"), children: draft.assignee }] : []),
        ...(draft.pub_date ? [{ key: "pd", label: t("research.emb.pub_date", "公开日"), children: draft.pub_date }] : []),
        { key: "origin", label: "origin", children: <Tag color="blue">{draft.origin}</Tag> },
        { key: "amount", label: "amount_source", children: <Tag>{amountSource}</Tag> },
        { key: "links", label: t("research.emb.links", "原文"), span: 2, children: (
          <Space>
            {draft.url && <a href={draft.url} target="_blank" rel="noreferrer noopener">Source</a>}
            {draft.url_alt && <a href={draft.url_alt} target="_blank" rel="noreferrer noopener">SureChEMBL</a>}
          </Space>
        ) },
      ]} />
      <Typography.Title level={5}>{t("research.emb.formulation", "配方草稿 · {{name}}", { name: active.formulation?.name ?? "" })}</Typography.Title>
      <Table<FormulationIngredient> size="small" pagination={false} rowKey={(r, i) => `${r.name}-${i}`} dataSource={ingredients}
        locale={{ emptyText: t("research.emb.empty", "无可抽取组分（未识别到配方表）") }}
        columns={[
          { title: t("research.emb.material", "原料"), dataIndex: "name" },
          { title: t("research.emb.role", "角色"), dataIndex: "role" },
          { title: "wt%", dataIndex: "weight_pct", align: "right" },
        ]} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        {!has ? t("research.emb.hint_empty", "空草稿不可确认入库；请先获得含表全文")
          : placeholder ? t("research.emb.hint_placeholder", "占位均分，须人审后才能使用") : t("research.emb.hint_table", "来自全文表，请人审后使用")}
      </Typography.Paragraph>
      {(active.formulation?.warnings?.length ?? 0) > 0 && (
        <List size="small" header={t("research.emb.warnings", "提示")} dataSource={active.formulation.warnings} renderItem={(w) => <List.Item>{w}</List.Item>} />
      )}
      <ErrorAlert error={confirm.error} />
      {note && <Alert type="success" showIcon message={note} style={{ marginTop: 12 }} />}
    </Drawer>
  );
}
