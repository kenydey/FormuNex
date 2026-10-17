import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Popconfirm, Select, Space, Switch, Tag, Tooltip, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, InboxOutlined, PlusOutlined, RedoOutlined, RollbackOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { AddSourceDrawer } from "@/features/library/AddSourceDrawer";
import { EmbodimentDraftDrawer } from "@/features/library/EmbodimentDraftDrawer";
import { KB_EXPORT_FORMATS, KB_EXPORT_MAX, type KbExportFormat } from "@/features/library/logic";
import { postDownload } from "@/features/library/shared";
import type { EmbodimentDraft, KbSource as Source } from "@/features/library/types";
import { useReparse } from "./reparse";

interface DeleteResult { chunks_removed: number; links_removed: number; wiki_pages_touched: number }

/** Knowledge-base documents: list, add (upload / identifier / URL / text / web search), archive, delete, export. */
export default function SourcesPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [archived, setArchived] = useState(false);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [format, setFormat] = useState<KbExportFormat>("docx");
  const [draft, setDraft] = useState<EmbodimentDraft | null>(null);

  const q = useQuery({
    queryKey: ["kb", "sources", projectId, archived],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: "500" });
      if (projectId) { qs.set("project_id", projectId); qs.set("include_global", "true"); }
      if (archived) qs.set("include_archived", "true");
      return call<{ sources: Source[]; total: number }>("GET", `/api/kb/sources?${qs}`);
    },
  });
  const ids = (q.data?.sources ?? []).map((s) => s.id);
  const eligible = useQuery({
    queryKey: ["kb", "sources", "eligibility", ids.slice(0, 100)], enabled: ids.length > 0,
    queryFn: () => call<{ items: { source_id: string; eligible: boolean }[] }>("POST", "/api/formulations/embodiment-eligibility", { source_ids: ids.slice(0, 100) }),
  });
  const isEligible = (id: string) => !!eligible.data?.items?.find((i) => i.source_id === id)?.eligible;
  const refresh = () => void qc.invalidateQueries({ queryKey: ["kb", "sources"] });

  const archive = useMutation({
    mutationFn: (v: { id: string; archived: boolean }) => call("POST", `/api/kb/sources/${encodeURIComponent(v.id)}/archive`, { archived: v.archived }),
    onSuccess: (_r, v) => { message.success(v.archived ? t("library.sources.archived_ok", "已归档") : t("library.sources.restored", "已恢复")); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => call<DeleteResult>("DELETE", `/api/kb/sources/${encodeURIComponent(id)}`),
    onSuccess: (r, id) => {
      message.success(t("library.sources.deleted", "已删除：切块 {{c}} · 图谱边 {{l}} · Wiki 页 {{w}}", { c: r.chunks_removed, l: r.links_removed, w: r.wiki_pages_touched }));
      setPicked((p) => p.filter((x) => x !== id));
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const exporting = useMutation({
    mutationFn: () => postDownload("/api/sources/export", { source_ids: picked.filter((id) => ids.includes(id)), format }, `sources_export.${format}`),
    onSuccess: (name) => message.success(t("library.sources.exported", "已导出 {{f}}", { f: name })),
    onError: (e) => message.error(errorText(e)),
  });
  const extract = useMutation({
    mutationFn: (id: string) => call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/formulations/extract-embodiment-draft", { source_id: id }),
    onSuccess: (r) => (r.ok && r.draft ? setDraft(r.draft) : message.warning(r.reason || t("library.search.no_draft", "无法提取实施例草稿"))),
    onError: (e) => message.error(errorText(e)),
  });
  const livePicked = picked.filter((id) => ids.includes(id));
  const { reparse, isRunning } = useReparse();

  return (
    <PageContainer extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setAdding(true)}>{t("library.sources.add", "添加数据源")}</Button>}>
      <ErrorAlert error={q.error} />
      <ProTable<Source>
        rowKey="id" loading={q.isLoading} dataSource={q.data?.sources} search={false} options={{ reload: () => void q.refetch() }}
        headerTitle={t("knowledge.sources.total", "共 {{n}} 篇", { n: q.data?.total ?? 0 })}
        rowSelection={{
          selectedRowKeys: livePicked, onChange: (keys) => setPicked((keys as string[]).slice(0, KB_EXPORT_MAX)),
          getCheckboxProps: (r) => ({ disabled: !livePicked.includes(r.id) && livePicked.length >= KB_EXPORT_MAX }),
        }}
        tableAlertRender={false}
        toolBarRender={() => [
          <Space key="archived"><Switch size="small" checked={archived} onChange={setArchived} />{t("library.sources.show_archived", "显示已归档")}</Space>,
          <Space.Compact key="export">
            <Select value={format} onChange={setFormat} style={{ width: 90 }} options={KB_EXPORT_FORMATS.map((f) => ({ value: f, label: f.toUpperCase() }))} />
            <Tooltip title={t("library.sources.export_tip", "勾选后导出为一个文件（摘要、表格清单与引用清单，最多 {{n}} 篇）", { n: KB_EXPORT_MAX })}>
              <Button icon={<DownloadOutlined />} disabled={!livePicked.length} loading={exporting.isPending} onClick={() => exporting.mutate()}>
                {t("library.sources.export", "导出 ({{n}})", { n: livePicked.length })}
              </Button>
            </Tooltip>
          </Space.Compact>,
        ]}
        columns={[
          { title: t("knowledge.sources.title", "标题"), dataIndex: "title", render: (_d, r) => (
            <Space size={4}>
              <Link to={`/knowledge/sources/${r.id}`}>{r.title || r.filename}</Link>
              {r.archived && <Tag>{t("library.sources.archived", "已归档")}</Tag>}
            </Space>) },
          { title: t("knowledge.sources.kind", "类型"), dataIndex: "source_kind", width: 120, render: (_d, r) => <Tag>{r.source_kind}</Tag> },
          { title: t("knowledge.sources.chars", "字数"), dataIndex: "raw_text_chars", width: 100, valueType: "digit" },
          { title: t("knowledge.sources.status", "解析"), dataIndex: "extraction_status", width: 110 },
          { title: t("knowledge.sources.project", "项目"), dataIndex: "project_id", width: 200, ellipsis: true,
            render: (_d, r) => r.project_id ?? <Tag>{t("knowledge.sources.global", "全局")}</Tag> },
          { title: t("common.actions", "操作"), width: 300, render: (_d, r) => (
            <Space size={0} wrap>
              <Popconfirm title={t("library.sources.reparse_confirm", "按当前解析配置重新解析原文件？将重建切块、索引与目录树。")}
                onConfirm={() => reparse(r.id)}>
                <Button type="link" size="small" icon={<RedoOutlined />} loading={isRunning(r.id)}>{t("library.sources.reparse", "重新解析")}</Button>
              </Popconfirm>
              {isEligible(r.id) && (
                <Button type="link" size="small" loading={extract.isPending && extract.variables === r.id} onClick={() => extract.mutate(r.id)}>
                  {t("library.search.draft", "提取实施例草稿")}
                </Button>
              )}
              <Button type="link" size="small" icon={r.archived ? <RollbackOutlined /> : <InboxOutlined />}
                onClick={() => archive.mutate({ id: r.id, archived: !r.archived })}>
                {r.archived ? t("library.sources.restore", "恢复") : t("library.sources.archive", "归档")}
              </Button>
              <Popconfirm title={t("library.sources.delete_confirm", "永久删除该文档？其切块、图谱链接与 Wiki 引用将一并移除，无法恢复。")}
                okButtonProps={{ danger: true }} onConfirm={() => remove.mutate(r.id)}>
                <Button type="link" size="small" danger icon={<DeleteOutlined />} loading={remove.isPending && remove.variables === r.id}>{t("common.delete", "删除")}</Button>
              </Popconfirm>
            </Space>) },
        ]}
      />
      <Typography.Text type="secondary">{t("library.sources.archive_hint", "归档 = 软删除：保留切块但默认不参与检索，可随时恢复；删除为永久操作。")}</Typography.Text>
      <AddSourceDrawer open={adding} onClose={() => setAdding(false)} />
      <EmbodimentDraftDrawer draft={draft} onClose={() => setDraft(null)} />
    </PageContainer>
  );
}
