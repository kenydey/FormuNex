import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Card, Collapse, Empty, Input, Space, Switch, Tag, Timeline, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { pages, type Chunk, type DocDepth } from "./types";

interface Result { query: string; stage1: Chunk[]; documents: DocDepth[] }

/** Two-stage retrieval: hybrid search, then table-of-contents navigation with an auditable trace (方案 §5.1). */
export default function DeepSearchPage() {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  const [deep, setDeep] = useState(true);
  const [llm, setLlm] = useState(true);
  const run = useMutation({
    mutationFn: (query: string) => call<Result>("POST", "/api/kb/pageindex/search",
      { query, top_k: 12, max_docs: 3, project_id: projectId, include_global: true, deep, use_llm: llm }),
  });
  return (
    <PageContainer subTitle={t("knowledge.deep.sub", "长文档（专利、标准）沿目录树逐节寻址：配方表 ↔ 性能表跨页关联，附推理轨迹与页码")}>
      <Card>
        <Input.Search size="large" enterButton loading={run.isPending}
          placeholder={t("knowledge.deep.placeholder", "例如：实施例 3 的配方及其耐盐雾结果")} onSearch={(q) => q.trim() && run.mutate(q)} />
        <Space style={{ marginTop: 12 }}>
          <Switch checked={deep} onChange={setDeep} /> {t("knowledge.deep.force", "所有文档都走第二阶段")}
          <Switch checked={llm} onChange={setLlm} /> {t("knowledge.deep.llm", "使用大模型导航（关闭则用启发式）")}
        </Space>
      </Card>
      <ErrorAlert error={run.error} />
      {run.data && !run.data.documents.length && <Empty style={{ marginTop: 24 }} />}
      {run.data?.documents.map((d) => (
        <ProCard key={d.source_id} bordered style={{ marginTop: 16 }} title={<Link to={`/knowledge/sources/${d.source_id}`}>{d.source_id}</Link>}
          extra={<Tag color={d.mode === "pageindex" ? "blue" : "default"}>{d.mode === "pageindex" ? t("knowledge.deep.tree", "目录树导航") : t("knowledge.deep.stage1", "仅第一阶段")}</Tag>}>
          {d.mode !== "pageindex" && <Alert type="info" message={d.reason} />}
          {d.navigation && (
            <ProCard split="vertical" ghost>
              <ProCard colSpan="40%" title={t("knowledge.deep.trace", "推理轨迹")}>
                <Timeline items={d.navigation.reasoning_trace.map((s) => ({
                  children: <div>
                    <Typography.Text strong>{s.title}</Typography.Text> {s.label && <Tag>{s.label}</Tag>} <Tag>{pages(s.page_range)}</Tag>
                    <div><Typography.Text type="secondary">{s.action}：{s.reason}</Typography.Text></div>
                  </div>,
                }))} />
                <Typography.Text type="secondary">
                  {t("knowledge.deep.budget", "跳数 {{h}} · 读取 {{c}} 字符 · 结束原因 {{s}}", { h: d.navigation.hops, c: d.navigation.chars_read, s: d.navigation.stopped })}
                </Typography.Text>
              </ProCard>
              <ProCard title={t("knowledge.deep.evidence", "证据")}>
                <Collapse items={d.navigation.evidence.map((e) => ({
                  key: e.node_id, label: <span>{e.title} {e.label && <Tag>{e.label}</Tag>} <Tag>{pages(e.page_range)}</Tag></span>,
                  children: <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{e.text}</Typography.Paragraph>,
                }))} />
              </ProCard>
            </ProCard>
          )}
        </ProCard>
      ))}
    </PageContainer>
  );
}
