import { PageContainer } from "@ant-design/pro-components";
import { Button, Card, Input, List, Slider, Space, Switch, Tag, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import type { Chunk } from "./types";

/** Hybrid (BM25 + vector) search over the knowledge base — stage 1 of the plan's retrieval (方案 §5.1). */
export default function SearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const [alpha, setAlpha] = useState(0.5);
  const [global, setGlobal] = useState(true);
  const search = useMutation({
    mutationFn: (query: string) => call<Chunk[]>("POST", "/api/kb/hybrid-search",
      { query, top_k: 20, alpha, project_id: projectId, include_global: global }),
  });
  return (
    <PageContainer subTitle={t("knowledge.search.sub", "混合检索：关键词 + 语义；结果可跳转到原文目录树")}
      extra={<Space>
        <Button onClick={() => navigate("/knowledge/research")}>{t("research.entry.external", "外部检索（专利 / 文献 / 网络）")}</Button>
        <Button onClick={() => navigate("/knowledge/research?tab=deep")}>{t("research.entry.deep", "深度研究报告")}</Button>
      </Space>}>
      <Card>
        <Input.Search size="large" enterButton loading={search.isPending}
          placeholder={t("search.placeholder", "检索知识库：配方、专利、标准、实施例…")} onSearch={(q) => q.trim() && search.mutate(q)} />
        <Space style={{ marginTop: 12 }} wrap>
          <span>{t("knowledge.search.alpha", "关键词 ← 权重 → 语义")}</span>
          <Slider style={{ width: 200 }} min={0} max={1} step={0.1} value={alpha} onChange={setAlpha} />
          <Switch checked={global} onChange={setGlobal} /> {t("knowledge.search.global", "包含全局知识库")}
        </Space>
      </Card>
      <ErrorAlert error={search.error} />
      <List
        style={{ marginTop: 16 }}
        dataSource={search.data ?? []}
        renderItem={(c) => (
          <Card size="small" style={{ marginBottom: 8 }}>
            <Space wrap style={{ marginBottom: 4 }}>
              <Link to={`/knowledge/sources/${c.source_id}`}>{String(c.meta?.title ?? c.source_id)}</Link>
              {c.page != null && <Tag>p.{c.page}</Tag>}
              {c.heading_path && <Typography.Text type="secondary">{c.heading_path}</Typography.Text>}
            </Space>
            <Typography.Paragraph ellipsis={{ rows: 4, expandable: true }} style={{ margin: 0 }}>{c.text}</Typography.Paragraph>
          </Card>
        )}
      />
    </PageContainer>
  );
}
