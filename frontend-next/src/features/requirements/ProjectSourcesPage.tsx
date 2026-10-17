import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Checkbox, Input, Popconfirm, Result, Space, Switch, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";
import { kbStatusLabel } from "./labels";
import { mergeSourceRows, parseEvidence, resolveOpenUrl, toggleSelected, type KbSource, type SourceRow } from "./sourceRows";

interface IngestResult { ok: boolean; status: string; source_id?: string | null; canonical_id?: string | null; reason?: string | null }

/**
 * 项目资料 (legacy knowledge-hub/HubMaterialsPane): the current project's knowledge-base documents merged with its
 * session evidence — tick for Q&A, re-ingest full text, archive / restore, permanently delete.
 */
export default function ProjectSourcesPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const params = useParams();
  const uiProject = useUi((s) => s.projectId);
  const setProject = useUi((s) => s.setProject);
  const projectId = params.projectId ?? uiProject ?? "";
  useEffect(() => { if (params.projectId) setProject(params.projectId); }, [params.projectId, setProject]);
  const [filter, setFilter] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);

  const project = useQuery({ queryKey: ["project", projectId], enabled: !!projectId, queryFn: () => call<ProjectDetail>("GET", `/api/projects/${encodeURIComponent(projectId)}`) });
  const kbKey = ["kb", "sources", projectId, includeArchived];
  const kb = useQuery({
    queryKey: kbKey, enabled: !!projectId,
    queryFn: () => call<{ sources: KbSource[]; total: number }>("GET", `/api/kb/sources?limit=200&project_id=${encodeURIComponent(projectId)}${includeArchived ? "&include_archived=true" : ""}`),
  });
  const evidence = useMemo(() => parseEvidence(project.data?.workspace.sources), [project.data]);
  const [selected, setSelected] = useState<string[]>([]);
  useEffect(() => { setSelected(Array.isArray(project.data?.workspace.selected_sources) ? project.data!.workspace.selected_sources! : []); }, [project.data]);
  const rows = useMemo(() => mergeSourceRows(evidence, selected, kb.data?.sources ?? [], filter), [evidence, selected, kb.data, filter]);

  const refreshKb = () => void qc.invalidateQueries({ queryKey: ["kb", "sources"] });
  const putWorkspace = (workspace: Record<string, unknown>) => call("PUT", `/api/projects/${encodeURIComponent(projectId)}`, { workspace });

  const select = useMutation({
    mutationFn: (next: string[]) => putWorkspace({ selected_sources: next }),
    onMutate: (next) => { const prev = selected; setSelected(next); return prev; },
    onError: (e, _n, prev) => { if (prev) setSelected(prev); message.error(errorText(e)); },
  });
  const ingest = useMutation({
    mutationFn: (r: SourceRow) => {
      const e = r.evidence;
      return call<IngestResult>("POST", "/api/kb/ingest-evidence", {
        identifier: r.identifier, title: r.title, url: r.url ?? undefined, url_alt: e?.url_alt ?? undefined, source: r.source, project_id: projectId,
        assignee: e?.assignee ?? undefined, pub_date: e?.pub_date ?? undefined, snippet: e?.snippet, oa_pdf_url: e?.oa_pdf_url ?? undefined,
        is_oa: e?.is_oa ?? undefined, relevance: e?.relevance ?? 0.9,
      });
    },
    onSuccess: (res, r) => {
      if (!res.ok) message.warning(res.reason || t("requirements.sources.ingest_failed", "入库失败"));
      else if (res.status === "skipped") message.info(t("requirements.sources.already", "已在库 · {{id}}", { id: res.canonical_id || r.identifier }));
      else message.success(t("requirements.sources.ingested", "全文已入库 · {{id}}", { id: res.canonical_id || r.identifier }));
      refreshKb();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const archive = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) => call("POST", `/api/kb/sources/${encodeURIComponent(id)}/archive`, { archived }),
    onSuccess: (_d, v) => { message.success(v.archived ? t("requirements.sources.archived", "已归档（可恢复）") : t("requirements.sources.restored", "已恢复")); refreshKb(); },
    onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: async (r: SourceRow) => {
      await call("DELETE", `/api/kb/sources/${encodeURIComponent(r.source_id!)}`);
      // Also drop it from the session evidence (the backend refuses to overwrite a non-empty list with an empty one).
      if (r.evidence) {
        const next = evidence.filter((e) => e.identifier !== r.identifier);
        if (next.length) await putWorkspace({ sources: next, selected_sources: selected.filter((x) => x !== r.identifier) });
      }
    },
    onSuccess: () => {
      message.success(t("requirements.sources.deleted", "已从知识库永久删除"));
      refreshKb();
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => message.error(errorText(e)),
  });

  if (!projectId) {
    return <PageContainer><Result status="info" title={t("requirements.sources.no_project", "未选择活动项目")}
      subTitle={t("requirements.sources.no_project_sub", "知识库按项目隔离，请先在顶部选择项目或打开一个项目。")}
      extra={<Link to="/spec/projects">{t("requirements.sources.go_projects", "前往项目列表")}</Link>} /></PageContainer>;
  }
  const busyRow = (r: SourceRow) =>
    (ingest.isPending && ingest.variables?.row_key === r.row_key) || (archive.isPending && archive.variables?.id === r.source_id) || (remove.isPending && remove.variables?.row_key === r.row_key);

  return (
    <PageContainer title={t("requirements.sources.title", "项目资料")} subTitle={project.data?.title}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={t("requirements.sources.scope", "仅显示当前项目知识库（不含其他项目 / 全局语料）· {{id}}", { id: projectId })} />
      <ErrorAlert error={project.error ?? kb.error} />
      <ProTable<SourceRow>
        rowKey="row_key" search={false} loading={kb.isLoading || project.isLoading} dataSource={rows} pagination={{ pageSize: 50 }}
        options={{ reload: () => { refreshKb(); void project.refetch(); } }}
        headerTitle={t("requirements.sources.total", "共 {{n}} 条", { n: rows.length })}
        toolBarRender={() => [
          <Input.Search key="f" allowClear placeholder={t("requirements.sources.filter", "筛选标题 / 公开号 / URL…")} onSearch={setFilter} onChange={(e) => !e.target.value && setFilter("")} style={{ width: 280 }} />,
          <Space key="a"><Switch size="small" checked={includeArchived} onChange={setIncludeArchived} />{t("requirements.sources.include_archived", "显示已归档")}</Space>,
        ]}
        locale={{ emptyText: t("requirements.sources.empty", "暂无资料。请先在知识检索中检索或上传，或等待后台自动入库。") }}
        columns={[
          { title: t("requirements.sources.pick", "选"), width: 50, tooltip: t("requirements.sources.pick_tip", "勾选用于问答"),
            render: (_d, r) => r.evidence
              ? <Checkbox checked={r.selected} disabled={select.isPending} onChange={() => select.mutate(toggleSelected(selected, r.identifier))} />
              : <Typography.Text type="secondary">—</Typography.Text> },
          { title: t("requirements.sources.col_title", "标题"), dataIndex: "title", ellipsis: true,
            render: (_d, r) => <Space direction="vertical" size={0}>
              {r.source_id ? <Link to={`/knowledge/sources/${r.source_id}`}>{r.title}</Link> : r.title}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.evidence ? t("requirements.sources.kind_session", "会话证据 + 知识库") : t("requirements.sources.kind_kb", "知识库")}</Typography.Text>
            </Space> },
          { title: t("requirements.sources.col_source", "来源"), dataIndex: "source", width: 110, render: (_d, r) => <Tag>{r.source}</Tag> },
          { title: t("requirements.sources.col_id", "URL / 标识"), dataIndex: "identifier", ellipsis: true, copyable: true, width: 260 },
          { title: t("requirements.sources.col_status", "状态"), width: 100, render: (_d, r) => r.kb_status
            ? <Tag color={r.kb_status === "failed" ? "error" : r.kb_status === "archived" ? "default" : r.kb_status === "indexed" ? "success" : "processing"}>{kbStatusLabel(t, r.kb_status)}</Tag> : "—" },
          { title: t("common.actions", "操作"), valueType: "option", width: 300, render: (_d, r) => {
            const url = resolveOpenUrl(r);
            const busy = busyRow(r);
            return [
              <Button key="open" type="link" size="small" disabled={!url} href={url ?? undefined} target="_blank" rel="noopener noreferrer">{t("requirements.sources.open", "打开")}</Button>,
              <Button key="ingest" type="link" size="small" disabled={busy || r.kb_status === "fetching" || r.kb_status === "indexing"}
                loading={ingest.isPending && ingest.variables?.row_key === r.row_key} onClick={() => ingest.mutate(r)}>
                {r.source_id ? t("requirements.sources.reingest", "再入库") : t("requirements.sources.ingest", "入库全文")}
              </Button>,
              r.source_id && (r.archived
                ? <Button key="restore" type="link" size="small" disabled={busy} onClick={() => archive.mutate({ id: r.source_id!, archived: false })}>{t("requirements.sources.restore", "恢复")}</Button>
                : <Popconfirm key="archive" title={t("requirements.sources.archive_confirm", "归档「{{t}}」？", { t: r.title })}
                    description={t("requirements.sources.archive_desc", "将从默认列表与检索中隐藏，切块保留，可随时恢复。")}
                    onConfirm={() => archive.mutateAsync({ id: r.source_id!, archived: true }).catch(() => undefined)}>
                    <Button type="link" size="small" disabled={busy}>{t("requirements.sources.archive", "归档")}</Button>
                  </Popconfirm>),
              r.source_id && <Popconfirm key="del" title={t("requirements.sources.delete_confirm", "永久删除「{{t}}」？", { t: r.title })}
                description={t("requirements.sources.delete_desc", "将移除切块与图谱提及，不可撤销（一般请用「归档」）。")} okButtonProps={{ danger: true }}
                onConfirm={() => remove.mutateAsync(r).catch(() => undefined)}>
                <Button type="link" size="small" danger disabled={busy}>{t("requirements.sources.delete", "永久删除")}</Button>
              </Popconfirm>,
            ].filter(Boolean);
          } },
        ]} />
    </PageContainer>
  );
}
