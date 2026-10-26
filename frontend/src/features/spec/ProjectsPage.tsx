import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Button, Space } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { DOMAINS } from "./types";
import { ProjectCreateModal } from "./ProjectCreateModal";
import { ProjectDeleteModal } from "./ProjectDeleteModal";
import { ProjectRenameModal, type ProjectRef } from "./ProjectRenameModal";

interface ProjectSummary { id: string; title: string; headline: string; domain: string; updated_at: string; has_doe: boolean; has_optimize: boolean }

export default function ProjectsPage() {
  const { t, i18n } = useTranslation();
  const { can } = useSession();
  const setProject = useUi((s) => s.setProject);
  const currentProject = useUi((s) => s.projectId);
  const [createOpen, setCreateOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ProjectRef | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectRef | null>(null);
  const q = useQuery({ queryKey: ["projects"], queryFn: () => call<ProjectSummary[]>("GET", "/api/projects") });
  const domainLabel = (d: string) => DOMAINS.find((x) => x[0] === d)?.[i18n.language === "en-US" ? 2 : 1] ?? d;
  const canWrite = can("project.write");
  return (
    <PageContainer>
      <ErrorAlert error={q.error} />
      <ProTable<ProjectSummary>
        rowKey="id" search={false} loading={q.isLoading} dataSource={q.data} options={{ reload: () => void q.refetch() }}
        toolBarRender={() => [<Button key="new" type="primary" disabled={!canWrite} title={t("common.need_project_write", "需要权限：创建 / 编辑项目")} onClick={() => setCreateOpen(true)}>{t("spec.new_project", "新建项目")}</Button>]}
        columns={[
          { title: t("spec.project", "项目"), dataIndex: "title", render: (_d, r) => <Link to={`/spec/projects/${r.id}`} onClick={() => setProject(r.id)}>{r.title}</Link> },
          { title: t("spec.domain", "产品领域"), dataIndex: "domain", render: (_d, r) => domainLabel(r.domain), width: 160 },
          { title: t("spec.headline", "摘要"), dataIndex: "headline", ellipsis: true },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", valueType: "dateTime", width: 170 },
          { title: t("common.actions", "操作"), valueType: "option", width: 150, render: (_d, r) => (
            <Space>
              <Button size="small" disabled={!canWrite} title={t("common.need_project_write", "需要权限：创建 / 编辑项目")}
                onClick={() => setRenameTarget({ id: r.id, title: r.title })}>{t("common.edit", "编辑")}</Button>
              <Button size="small" danger disabled={!canWrite} title={t("common.need_project_write", "需要权限：创建 / 编辑项目")}
                onClick={() => setDeleteTarget({ id: r.id, title: r.title })}>{t("common.delete", "删除")}</Button>
            </Space>
          ) },
        ]}
      />
      <ProjectCreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <ProjectRenameModal open={!!renameTarget} onClose={() => setRenameTarget(null)} project={renameTarget} />
      <ProjectDeleteModal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} project={deleteTarget}
        onDeleted={(id) => { if (id === currentProject) setProject(null); }} />
    </PageContainer>
  );
}
