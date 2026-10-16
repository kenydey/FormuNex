import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";

interface Source { id: string; title: string; filename: string; source_kind: string; project_id: string | null; raw_text_chars: number; extraction_status: string; archived: boolean }

export default function SourcesPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["kb", "sources"], queryFn: () => call<{ sources: Source[]; total: number }>("GET", "/api/kb/sources") });
  return (
    <PageContainer>
      <ErrorAlert error={q.error} />
      <ProTable<Source>
        rowKey="id" loading={q.isLoading} dataSource={q.data?.sources} search={false} options={{ reload: () => void q.refetch() }}
        headerTitle={t("knowledge.sources.total", "共 {{n}} 篇", { n: q.data?.total ?? 0 })}
        columns={[
          { title: t("knowledge.sources.title", "标题"), dataIndex: "title", render: (_d, r) => <Link to={`/knowledge/sources/${r.id}`}>{r.title || r.filename}</Link> },
          { title: t("knowledge.sources.kind", "类型"), dataIndex: "source_kind", width: 120, render: (_d, r) => <Tag>{r.source_kind}</Tag> },
          { title: t("knowledge.sources.chars", "字数"), dataIndex: "raw_text_chars", width: 100, valueType: "digit" },
          { title: t("knowledge.sources.status", "解析"), dataIndex: "extraction_status", width: 110 },
          { title: t("knowledge.sources.project", "项目"), dataIndex: "project_id", width: 200, ellipsis: true,
            render: (_d, r) => r.project_id ?? <Tag>{t("knowledge.sources.global", "全局")}</Tag> },
        ]}
      />
    </PageContainer>
  );
}
