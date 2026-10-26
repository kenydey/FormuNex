import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Empty, Popconfirm, Select, Space, Tabs, Tag, Tree, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { groupChunksByPage, pages, type Chunk, type TocNode } from "./types";
import { useReparse } from "./reparse";
import { EmbodimentDraftDrawer } from "@/features/library/EmbodimentDraftDrawer";
import { ExtractionTablesPanel, TableBadges } from "@/features/library/SourceTables";
import { PdfPreview } from "@/shared/PdfPreview";
import type { EmbodimentDraft, SourceDetail } from "@/features/library/types";

interface KgRebuildReport { linked_sources: number; entities_upserted: number; mentions_upserted: number; links_created: number; relations_upserted: number; failed_sources: number }

interface TreeResponse { source_id: string; doc_kind: string; node_count: number; max_depth: number; page_count: number; summaries_status: string; built_at: string; tree: TocNode }

const KIND_COLOR: Record<string, string> = { example: "green", table: "blue", section: "purple", heading: "default", root: "default" };

function toTree(n: TocNode): { key: string; title: React.ReactNode; children: unknown[] } {
  return {
    key: n.node_id,
    title: <span>{n.label && <Tag color={KIND_COLOR[n.kind]}>{n.label}</Tag>}{n.title} <Typography.Text type="secondary">{pages(n.page_range)}</Typography.Text>
      {n.refs.length > 0 && <Tag style={{ marginLeft: 4 }}>↔ {n.refs.length}</Tag>}</span>,
    children: n.children.map(toTree),
  };
}

function find(n: TocNode, id: string): TocNode | null {
  if (n.node_id === id) return n;
  for (const c of n.children) {
    const f = find(c, id);
    if (f) return f;
  }
  return null;
}

/** PageIndex table-of-contents tree of one document with the chunks behind each node (方案 §5.1). */
export default function SourceDetailPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const { sourceId = "" } = useParams();
  const [selected, setSelected] = useState<string | null>(null);
  const tree = useQuery({
    queryKey: ["kb", "toc", sourceId], retry: false,
    queryFn: () => call<TreeResponse>("GET", `/api/kb/sources/${sourceId}/toc-tree`),
  });
  const chunks = useQuery({
    queryKey: ["kb", "chunks", sourceId],
    // P1-2: page through the whole source — the backend caps one response
    // at 200 chunks, and a single first page silently truncated long
    // documents (tree nodes past chunk 200 rendered Empty). Loop until
    // the page is short or the reported total is reached.
    queryFn: async () => {
      const out: Chunk[] = [];
      const PAGE = 200;
      let offset = 0;
      for (;;) {
        const d = await call<Chunk[] | { chunks: Chunk[]; total?: number }>(
          "GET", `/api/kb/chunks/by-source/${sourceId}?limit=${PAGE}&offset=${offset}`);
        const page = Array.isArray(d) ? d : d?.chunks ?? [];
        out.push(...page);
        const total = Array.isArray(d) ? undefined : d?.total;
        if (page.length < PAGE) break;
        if (total != null && out.length >= total) break;
        offset += page.length;
      }
      return out;
    },
  });
  const rebuild = useMutation({
    mutationFn: () => call("POST", `/api/kb/sources/${sourceId}/toc-tree/rebuild`),
    onSuccess: () => { message.success(t("knowledge.tree.rebuilt", "目录树已重建")); void tree.refetch(); },
    onError: (e) => message.error(errorText(e)),
  });
  const allChunks = useMemo(() => chunks.data ?? [], [chunks.data]);
  const node = selected && tree.data ? find(tree.data.tree, selected) : null;
  const nodeChunks = node ? allChunks.filter((c) => node.chunk_ids.includes(c.id)) : [];
  const detail = useQuery({
    queryKey: ["kb", "source-detail", sourceId], retry: false,
    queryFn: () => call<SourceDetail>("GET", `/api/sources/${encodeURIComponent(sourceId)}/detail`),
  });
  const [draft, setDraft] = useState<EmbodimentDraft | null>(null);
  const [alignPage, setAlignPage] = useState<number | "unpaged" | undefined>(undefined);
  const [kgReport, setKgReport] = useState<KgRebuildReport | null>(null);
  const rebuildKg = useMutation({
    mutationFn: () => call<KgRebuildReport>("POST", "/api/kg/rebuild", {}),
    onSuccess: (r) => {
      setKgReport(r);
      message.success(t("knowledge.kg.rebuilt", "知识图谱已重建：实体 {{e}}，关系 {{r}}", { e: r.entities_upserted, r: r.relations_upserted }));
    },
    onError: (e) => message.error(errorText(e)),
  });
  const extract = useMutation({
    mutationFn: () => call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/formulations/extract-embodiment-draft", { source_id: sourceId }),
    onSuccess: (r) => (r.ok && r.draft ? setDraft(r.draft) : message.warning(r.reason || t("library.search.no_draft", "无法提取实施例草稿"))),
    onError: (e) => message.error(errorText(e)),
  });
  const missing = tree.error instanceof ApiError && tree.error.status === 404;
  const { reparse, isRunning } = useReparse();
  const pageGroups = useMemo(() => groupChunksByPage(allChunks), [allChunks]);
  const firstPaged = pageGroups.find((g) => g.page != null)?.page;
  const effectivePage = alignPage === undefined ? firstPaged : alignPage;
  const alignChunks = effectivePage === "unpaged" || effectivePage == null
    ? allChunks.filter((c) => c.page == null)
    : allChunks.filter((c) => c.page === effectivePage);

  return (
    <PageContainer title={detail.data?.title || sourceId} extra={<Space>
      <Button loading={extract.isPending} disabled={!can("formulation.write")} onClick={() => extract.mutate()}>{t("library.search.draft", "提取实施例草稿")}</Button>
      <Popconfirm title={t("knowledge.kg.rebuild_confirm", "重建全库知识图谱？将重新抽取全部来源的实体与关系，可能需要一段时间。")}
        onConfirm={() => rebuildKg.mutate()}>
        <Button loading={rebuildKg.isPending} disabled={!can("knowledge.write")}>{t("knowledge.kg.rebuild", "一键生成 KG")}</Button>
      </Popconfirm>
      <Button loading={rebuild.isPending} disabled={!can("knowledge.write")} onClick={() => rebuild.mutate()}>{t("knowledge.tree.rebuild", "重建目录树")}</Button>
      <Popconfirm title={t("library.sources.reparse_confirm", "按当前解析配置重新解析原文件？将重建切块、索引与目录树。")}
        onConfirm={() => reparse(sourceId)}>
        <Button loading={isRunning(sourceId)} disabled={!can("knowledge.write")}>{t("library.sources.reparse", "重新解析")}</Button>
      </Popconfirm></Space>}>
      {kgReport && (
        <Alert type="success" showIcon closable onClose={() => setKgReport(null)} style={{ marginBottom: 12 }}
          message={t("knowledge.kg.report", "KG 重建完成：关联来源 {{s}}，实体 {{e}}，提及 {{m}}，关系 {{r}}，失败来源 {{f}}",
            { s: kgReport.linked_sources, e: kgReport.entities_upserted, m: kgReport.mentions_upserted, r: kgReport.relations_upserted, f: kgReport.failed_sources })} />
      )}
      <Tabs items={[
        { key: "tree", label: t("knowledge.tree.title", "目录树"), children: <>
      {!missing && <ErrorAlert error={tree.error} />}
      {tree.data && (
        <Descriptions size="small" column={5} style={{ marginBottom: 12 }}>
          <Descriptions.Item label={t("knowledge.tree.kind", "文档类型")}>{tree.data.doc_kind}</Descriptions.Item>
          <Descriptions.Item label={t("knowledge.tree.nodes", "节点")}>{tree.data.node_count}</Descriptions.Item>
          <Descriptions.Item label={t("knowledge.tree.depth", "深度")}>{tree.data.max_depth}</Descriptions.Item>
          <Descriptions.Item label={t("knowledge.tree.pages", "页数")}>{tree.data.page_count}</Descriptions.Item>
          <Descriptions.Item label={t("knowledge.tree.summaries", "节点摘要")}>{tree.data.summaries_status}</Descriptions.Item>
        </Descriptions>
      )}
      <ProCard split="vertical" bordered>
        <ProCard colSpan="40%" title={t("knowledge.tree.title", "目录树")} style={{ maxHeight: 720, overflow: "auto" }}>
          {missing ? <Empty description={t("knowledge.tree.none", "该文档尚无目录树，可点击“重建目录树”")} />
            : tree.data && <Tree defaultExpandAll treeData={[toTree(tree.data.tree)] as never} onSelect={(k) => setSelected((k[0] as string) ?? null)} />}
        </ProCard>
        <ProCard title={node ? node.title : t("knowledge.tree.pick", "选择左侧节点查看原文")} style={{ maxHeight: 720, overflow: "auto" }}>
          {node?.summary && <Typography.Paragraph type="secondary">{node.summary}</Typography.Paragraph>}
          {nodeChunks.map((c) => (
            <Typography.Paragraph key={c.id} style={{ whiteSpace: "pre-wrap" }}>
              {c.page != null && <Tag>p.{c.page}</Tag>}{c.text}
            </Typography.Paragraph>
          ))}
          {node && !nodeChunks.length && <Empty />}
        </ProCard>
      </ProCard>
        </> },
        { key: "align", label: t("knowledge.align.title", "原文对照"), children: <>
          <Typography.Paragraph type="secondary">
            {t("knowledge.align.help", "左栏为解析切块（按页分组），右栏为原文件 PDF；选页或点切块可同步跳转。切块无 bbox 坐标（后端未暴露），故为页级对照而非框级对照；MinerU 表格的 bbox 见“结构化抽取”页。")}
          </Typography.Paragraph>
          <Space style={{ marginBottom: 12 }} wrap>
            <Select style={{ width: 180 }} value={effectivePage === "unpaged" || effectivePage == null ? "unpaged" : String(effectivePage)}
              onChange={(v) => setAlignPage(v === "unpaged" ? "unpaged" : Number(v))}
              options={pageGroups.map((g) => ({
                value: g.page == null ? "unpaged" : String(g.page),
                label: g.page == null
                  ? t("knowledge.align.unpaged", "未标页") + ` (${g.chunks.length})`
                  : t("knowledge.align.page", "第 {{p}} 页", { p: g.page }) + ` (${g.chunks.length})`,
              }))} />
            <Typography.Text type="secondary">
              {t("knowledge.align.count", "共 {{n}} 切块", { n: allChunks.length })}
            </Typography.Text>
          </Space>
          <ProCard split="vertical" bordered>
            <ProCard colSpan="50%" style={{ maxHeight: 720, overflow: "auto" }} title={t("knowledge.align.chunks", "解析切块")}>
              {alignChunks.map((c) => (
                <Typography.Paragraph key={c.id} style={{ whiteSpace: "pre-wrap", cursor: c.page != null ? "pointer" : undefined }}
                  onClick={() => { if (c.page != null) setAlignPage(c.page); }}>
                  {c.page != null && <Tag>p.{c.page}</Tag>}
                  {c.heading_path && <Typography.Text type="secondary">{c.heading_path} </Typography.Text>}
                  {c.text}
                </Typography.Paragraph>
              ))}
              {!alignChunks.length && <Empty />}
            </ProCard>
            <ProCard title={t("knowledge.align.pdf", "原文件")}>
              <PdfPreview path={`/api/documents/${encodeURIComponent(sourceId)}/file`}
                page={typeof effectivePage === "number" ? effectivePage : undefined} height={720} />
            </ProCard>
          </ProCard>
        </> },
        { key: "tables", label: t("library.detail.tables", "表格资产"), children: <TableBadges sourceId={sourceId} /> },
        { key: "extract", label: t("library.detail.extract", "结构化抽取"), children: <ExtractionTablesPanel sourceId={sourceId} /> },
        { key: "info", label: t("library.detail.info", "文档信息"), children: <>
          <ErrorAlert error={detail.error} />
          {detail.data && (
            <Descriptions size="small" bordered column={2}>
              <Descriptions.Item label={t("library.detail.filename", "文件名")}>{detail.data.filename}</Descriptions.Item>
              <Descriptions.Item label={t("knowledge.sources.kind", "类型")}><Tag>{detail.data.source_kind}</Tag></Descriptions.Item>
              <Descriptions.Item label={t("knowledge.sources.chars", "字数")}>{detail.data.raw_text_chars}</Descriptions.Item>
              <Descriptions.Item label={t("knowledge.sources.status", "解析")}>{detail.data.extraction_status}</Descriptions.Item>
              <Descriptions.Item label={t("library.detail.fulltext", "全文")}>{detail.data.has_fulltext ? t("common.yes", "是") : t("common.no", "否")}</Descriptions.Item>
              <Descriptions.Item label={t("library.detail.chunks", "切块")}>{detail.data.total_chunks}</Descriptions.Item>
              <Descriptions.Item label={t("common.created", "创建时间")}>{detail.data.created_at}</Descriptions.Item>
              <Descriptions.Item label={t("library.detail.lineage", "上游血缘")}>{detail.data.provenance_upstream.length}</Descriptions.Item>
            </Descriptions>
          )}
        </> },
      ]} />
      <EmbodimentDraftDrawer draft={draft} onClose={() => setDraft(null)} />
    </PageContainer>
  );
}
