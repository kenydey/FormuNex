import { ModalForm, PageContainer, ProFormText, ProTable } from "@ant-design/pro-components";
import { App, Button, Switch, Tag, Tooltip, Typography } from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { EmbodimentDraftDrawer } from "./EmbodimentDraftDrawer";
import type { EligibilityItem, EmbodimentDraft, KbSource } from "./types";
import { useProjectRequirement } from "./useProjectRequirement";

type Extracted = { ok: boolean; draft?: EmbodimentDraft; reason?: string };

/**
 * 实施例草稿 (P3.1): pick an ingested patent / paper whose fulltext has formulation tables (PageIndex-parsed), extract
 * a review-only embodiment draft, or extract a SureChEMBL example draft by document id; confirm via the review drawer.
 */
export default function EmbodimentPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { projectId, domain } = useProjectRequirement();
  const [onlyEligible, setOnlyEligible] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<EmbodimentDraft | null>(null);

  const sources = useQuery({
    queryKey: ["kb", "sources", "research-embodiment", projectId],
    queryFn: () => call<{ sources: KbSource[] }>("GET", `/api/kb/sources?limit=200${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`),
  });
  const ids = (sources.data?.sources ?? []).map((s) => s.id).filter(Boolean).slice(0, 100);
  const eligibility = useQuery({
    queryKey: ["research", "embodiment-eligibility", ids], enabled: ids.length > 0,
    queryFn: () => call<{ items: EligibilityItem[] }>("POST", "/api/formulations/embodiment-eligibility", { source_ids: ids }),
  });
  const elig = new Map((eligibility.data?.items ?? []).map((i) => [i.source_id, i]));
  const rows = (sources.data?.sources ?? []).filter((s) => !onlyEligible || elig.get(s.id)?.eligible);

  const open = async (key: string, f: () => Promise<Extracted>) => {
    setBusy(key);
    try {
      const res = await f();
      if (!res.ok || !res.draft) {
        message.warning(res.reason || t("research.emb.no_draft", "无法提取实施例草稿"));
        return false;
      }
      setDraft(res.draft);
      return true;
    } catch (e) {
      message.error(errorText(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  return (
    <PageContainer subTitle={t("research.emb.sub", "从已入库全文（PageIndex 解析的配方表）或 SureChEMBL 提取实施例草稿，人工确认后写入 KG 与待审原料")}>
      <ErrorAlert error={sources.error ?? eligibility.error} />
      <ProTable<KbSource>
        rowKey="id" search={false} size="small" loading={sources.isLoading || eligibility.isLoading} dataSource={rows}
        options={{ reload: () => { void sources.refetch(); void eligibility.refetch(); } }}
        headerTitle={<span><Switch size="small" checked={onlyEligible} onChange={setOnlyEligible} /> {t("research.emb.only_eligible", "仅显示可提取（含配方表信号）")}</span>}
        toolBarRender={() => [
          <ModalForm<{ doc_id: string; title?: string; assignee?: string; pub_date?: string; url?: string }> key="sc"
            title={t("research.emb.sc_title", "SureChEMBL 实施例草稿")} modalProps={{ destroyOnHidden: true }}
            trigger={<Button icon={<ExperimentOutlined />}>{t("research.emb.sc", "按 SureChEMBL 文献号提取")}</Button>}
            onFinish={(v) => open("sc", () => call<Extracted>("POST", "/api/surechembl/extract-example-draft", { ...v, domain }))}>
            <ProFormText name="doc_id" label={t("research.emb.doc_id", "文献号")} placeholder="WO-2020123456-A1" rules={[{ required: true, whitespace: true }]} />
            <ProFormText name="title" label={t("research.emb.doc_title", "标题")} />
            <ProFormText name="assignee" label={t("research.emb.assignee", "专利权人")} />
            <ProFormText name="pub_date" label={t("research.emb.pub_date", "公开日")} />
            <ProFormText name="url" label="URL" />
            <Typography.Text type="secondary">{t("research.emb.sc_hint", "未入库全文时比重为占位均分；入库全文后可提取真实比重。")}</Typography.Text>
          </ModalForm>,
        ]}
        locale={{ emptyText: t("research.emb.no_sources", "暂无可提取的已入库全文。可在「外部检索」中对专利 / 文献「入库全文」。") }}
        columns={[
          { title: t("research.emb.doc_title", "标题"), dataIndex: "title", ellipsis: true,
            render: (_d, s) => <Link to={`/knowledge/sources/${s.id}`}>{s.title || s.filename || s.id}</Link> },
          { title: t("research.emb.kind", "类型"), dataIndex: "source_kind", width: 120, render: (_d, s) => <Tag>{s.source_kind || "doc"}</Tag> },
          { title: t("research.emb.chars", "字数"), dataIndex: "raw_text_chars", width: 90,
            render: (_d, s) => (s.raw_text_chars ? `${Math.round(s.raw_text_chars / 1000)}k` : "—") },
          { title: t("research.emb.eligible", "可提取"), width: 120, render: (_d, s) => {
            const e = elig.get(s.id);
            if (!e) return "—";
            return <Tooltip title={e.reason ?? undefined}>
              <Tag color={e.eligible ? "success" : "default"}>{e.eligible ? (e.has_table_signal ? t("research.emb.has_table", "含配方表") : t("research.emb.yes", "可提取")) : t("research.emb.no", "不可")}</Tag>
            </Tooltip>;
          } },
          { title: t("common.actions", "操作"), valueType: "option", width: 160, render: (_d, s) => [
            <Button key="x" type="link" size="small" disabled={!elig.get(s.id)?.eligible} loading={busy === s.id}
              onClick={() => void open(s.id, () => call<Extracted>("POST", "/api/formulations/extract-embodiment-draft", { source_id: s.id, domain }))}>
              {t("research.emb.extract_draft", "提取实施例草稿")}
            </Button>,
          ] },
        ]}
      />
      <EmbodimentDraftDrawer draft={draft} onClose={() => setDraft(null)} />
    </PageContainer>
  );
}
