import { PageContainer, ProCard } from "@ant-design/pro-components";
import { App, Button, Descriptions, Empty, Tag, Tree, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { pages, type Chunk, type TocNode } from "./types";

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
  const { sourceId = "" } = useParams();
  const [selected, setSelected] = useState<string | null>(null);
  const tree = useQuery({
    queryKey: ["kb", "toc", sourceId], retry: false,
    queryFn: () => call<TreeResponse>("GET", `/api/kb/sources/${sourceId}/toc-tree`),
  });
  const chunks = useQuery({
    queryKey: ["kb", "chunks", sourceId],
    queryFn: () => call<Chunk[] | { chunks: Chunk[] }>("GET", `/api/kb/chunks/by-source/${sourceId}`),
  });
  const rebuild = useMutation({
    mutationFn: () => call("POST", `/api/kb/sources/${sourceId}/toc-tree/rebuild`),
    onSuccess: () => { message.success(t("knowledge.tree.rebuilt", "目录树已重建")); void tree.refetch(); },
    onError: (e) => message.error(errorText(e)),
  });
  const allChunks = useMemo(() => {
    const d = chunks.data;
    return Array.isArray(d) ? d : d?.chunks ?? [];
  }, [chunks.data]);
  const node = selected && tree.data ? find(tree.data.tree, selected) : null;
  const nodeChunks = node ? allChunks.filter((c) => node.chunk_ids.includes(c.id)) : [];
  const missing = tree.error instanceof ApiError && tree.error.status === 404;

  return (
    <PageContainer title={sourceId} extra={<Button loading={rebuild.isPending} onClick={() => rebuild.mutate()}>{t("knowledge.tree.rebuild", "重建目录树")}</Button>}>
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
    </PageContainer>
  );
}
