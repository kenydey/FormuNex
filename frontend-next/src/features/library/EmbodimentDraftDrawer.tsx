import { Alert, App, Button, Descriptions, Drawer, Segmented, Space, Table, Tag } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { draftConfirmPath, draftForEmbodiment } from "./logic";
import type { EmbodimentDraft, EmbodimentIngredient } from "./types";

interface ConfirmResult { ok: boolean; pending_materials?: { action: string }[]; formulation_entity_id?: string; promoted_to_pool?: boolean; note?: string }

/** Human review gate of an embodiment draft: confirm writes KG + pending materials only, never the production pool. */
export function EmbodimentDraftDrawer({ draft, onClose }: { draft: EmbodimentDraft | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [idx, setIdx] = useState(0);
  const [done, setDone] = useState<string | null>(null);
  const current = useMemo(() => (draft ? draftForEmbodiment(draft, idx) : null), [draft, idx]);
  const ingredients: EmbodimentIngredient[] = current?.formulation?.ingredients ?? [];
  const confirm = useMutation({
    mutationFn: async () => {
      const r = await call<ConfirmResult>("POST", draftConfirmPath(current!.origin), { draft: current });
      if (!r.ok) throw new Error(t("library.draft.failed", "确认失败"));
      return r;
    },
    onSuccess: (r) => {
      if (r.promoted_to_pool) { message.error(t("library.draft.gate_breach", "安全闸失败：不应写入生产配方池")); return; }
      const pending = (r.pending_materials ?? []).filter((p) => p.action === "pending").length;
      const note = r.note || t("library.draft.done", "已确认：{{n}} 条原料进入 pending；配方实体 {{id}}；未写入生产配方池",
        { n: pending, id: r.formulation_entity_id ?? "—" });
      setDone(note);
      message.success(note);
    },
  });
  const close = () => { setIdx(0); setDone(null); confirm.reset(); onClose(); };
  const embs = draft?.embodiments ?? [];
  const placeholder = (current?.amount_source || "placeholder") === "placeholder";

  return (
    <Drawer open={!!draft} onClose={close} width={760} destroyOnHidden
      title={t("library.draft.title", "实施例草稿 · {{id}}", { id: draft?.doc_id || draft?.source_id || "" })}
      extra={<Button type="primary" loading={confirm.isPending} disabled={!ingredients.length || !!done} onClick={() => confirm.mutate()}>
        {t("library.draft.confirm", "人工确认入库")}</Button>}>
      {current && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="warning" showIcon message={t("library.draft.gate", "人审闸：确认后仅写入知识图谱 + 原料 pending，不进入生产配方池")} />
          {placeholder && <Alert type="info" showIcon message={t("library.draft.placeholder", "比重为占位均分（未识别到配方表）；入库全文后可提取真实比重")} />}
          <ErrorAlert error={confirm.error} />
          {done && <Alert type="success" showIcon message={done} />}
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label={t("library.draft.doc_title", "标题")} span={2}>{current.title || "—"}</Descriptions.Item>
            <Descriptions.Item label={t("library.draft.origin", "来源")}><Tag>{current.origin}</Tag></Descriptions.Item>
            <Descriptions.Item label={t("library.draft.amount", "比重来源")}><Tag>{current.amount_source || "placeholder"}</Tag></Descriptions.Item>
            <Descriptions.Item label={t("library.draft.assignee", "申请人")}>{current.assignee || "—"}</Descriptions.Item>
            <Descriptions.Item label={t("library.draft.pub_date", "公开日")}>{current.pub_date || "—"}</Descriptions.Item>
          </Descriptions>
          {embs.length > 1 && (
            <Segmented value={idx} onChange={(v) => setIdx(Number(v))}
              options={embs.map((e, i) => ({ value: i, label: `${e.label}${e.page_hint != null ? ` · p.${e.page_hint}` : ""}` }))} />
          )}
          <Table<EmbodimentIngredient> size="small" pagination={false} rowKey={(r) => `${r.name}-${r.role}`} dataSource={ingredients}
            locale={{ emptyText: t("library.draft.empty", "无可确认组分：未识别到配方表") }}
            columns={[
              { title: t("library.draft.name", "组分"), dataIndex: "name" },
              { title: t("library.draft.role", "角色"), dataIndex: "role", width: 140, render: (v: string) => <Tag>{v}</Tag> },
              { title: "wt%", dataIndex: "weight_pct", width: 100, align: "right", render: (v: number) => (typeof v === "number" ? v.toFixed(2) : "—") },
            ]} />
          {[...(embs[idx]?.warnings ?? []), ...(current.formulation?.warnings ?? [])].map((w) => (
            <Alert key={w} type="warning" message={w} />
          ))}
        </Space>
      )}
    </Drawer>
  );
}
