import { ProTable } from "@ant-design/pro-components";
import { Alert, App, Badge, Button, Card, Checkbox, Input, Popconfirm, Progress, Space, Tag, Tooltip, Typography, Upload } from "antd";
import { CloudDownloadOutlined, DeleteOutlined, ExperimentOutlined, FileTextOutlined, UploadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, cancelTask, type AsyncTaskAccepted } from "@/api/stream";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { EmbodimentDraftDrawer } from "./EmbodimentDraftDrawer";
import {
  citationTarget, evidenceKey, mapKbSourceIds, pageLabel, parseKbIngest, parseSearchData, resolveItems, sourceBadge, SOURCE_TYPES,
  type KbResolveMatch,
} from "./lib";
import { useResearch } from "./store";
import type {
  EmbodimentDraft, Evidence, IngestEvidenceResponse, KbIngestDoc, KbIngestProgress, SearchProgress, SearchResponse, SourceStatus, SourceType,
} from "./types";
import { useProjectRequirement } from "./useProjectRequirement";

interface KbBuild { taskId: string; progress: KbIngestProgress | null; message: string; active: boolean }
type RowState = { busy?: "ingest" | "draft"; sourceId?: string | null; status?: string; error?: string | null };

/** Federated external search (patents / SureChEMBL / literature / internet / NotebookLM) that gathers the sources. */
export default function ExternalSearchTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const { projectId, requirement, domain } = useProjectRequirement();
  const s = useResearch();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<SearchProgress | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [fallback, setFallback] = useState<string | null>(null);
  const [kb, setKb] = useState<KbBuild | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  /** Row key → KB source id holding the hit's full text (background KB build + resolve endpoint). */
  const [kbMap, setKbMap] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<EmbodimentDraft | null>(null);
  const [uploading, setUploading] = useState(false);
  const abort = useRef<{ ctrl: AbortController; taskId?: string } | null>(null);

  const status = useQuery({
    queryKey: ["research", "source-status"],
    queryFn: () => call<Record<string, SourceStatus>>("GET", "/api/search/status"),
    staleTime: 60_000,
  });
  const patchRow = (k: string, p: RowState) => setRows((r) => ({ ...r, [k]: { ...r[k], ...p } }));
  const sourceIdOf = (k: string) => rows[k]?.sourceId || kbMap[k] || null;

  /** Map hits to their KB documents: progress docs first, then one resolve call for whatever is still unmapped. */
  const resolveKb = async (docs: KbIngestDoc[] = []) => {
    const all = useResearch.getState().sources;
    const local = mapKbSourceIds(all, docs);
    let matches: KbResolveMatch[] = [];
    const items = resolveItems(all, { ...kbMap, ...local });
    if (items.length) {
      try {
        matches = (await call<{ matches: KbResolveMatch[] }>("POST", "/api/kb/sources/resolve", { items })).matches ?? [];
      } catch {
        matches = []; // fail-open: rows simply stay unmapped
      }
    }
    const mapped = { ...local, ...mapKbSourceIds(all, [], matches) };
    if (Object.keys(mapped).length) setKbMap((m) => ({ ...m, ...mapped }));
  };

  const trackKb = async (taskId: string) => {
    setKb({ taskId, progress: null, message: t("research.kb.building", "知识库后台构建中…"), active: true });
    try {
      const final = await awaitTask(taskId, { onEvent: (ev) => {
        const p = parseKbIngest(ev.data);
        if (p) {
          const live = mapKbSourceIds(useResearch.getState().sources, p.docs);
          if (Object.keys(live).length) setKbMap((m) => ({ ...m, ...live }));
        }
        setKb((k) => k && k.taskId === taskId ? { ...k, progress: p ?? k.progress, message: ev.message || k.message } : k);
      } });
      const finalProgress = parseKbIngest(final.data);
      setKb((k) => k && k.taskId === taskId ? { ...k, progress: finalProgress ?? k.progress,
        message: final.message || t("research.kb.done", "知识库构建完成"), active: false } : k);
      void qc.invalidateQueries({ queryKey: ["kb", "sources"] });
      await resolveKb(finalProgress?.docs ?? []);
    } catch (e) {
      setKb((k) => k && k.taskId === taskId ? { ...k, active: false,
        message: t("research.kb.untracked", "已停止跟踪进度（{{e}}）——后台任务可能仍在继续", { e: errorText(e) }) } : k);
    }
  };

  const absorb = (r: ReturnType<typeof parseSearchData>) => {
    if (r.evidence.length) s.addSources(r.evidence);
    s.setSearchMeta({ usedSeedFallback: r.usedSeedFallback || undefined, filterReport: r.filterReport ?? undefined });
    if (r.sourceStatus) qc.setQueryData(["research", "source-status"], r.sourceStatus);
  };

  const run = async (append: boolean) => {
    const query = s.query.trim();
    if (!query || busy) return;
    const types = s.sourceTypes;
    if (types.includes("notebooklm") && !s.notebookId.trim()) {
      setError(new Error(t("research.search.need_notebook", "请先填写 NotebookLM Notebook ID")));
      return;
    }
    const body = {
      query, requirement: requirement ?? undefined, source_types: types.length ? types : undefined, total_limit: 300,
      notebooklm_notebook_id: types.includes("notebooklm") ? s.notebookId.trim() : undefined,
    };
    const ctrl = new AbortController();
    abort.current = { ctrl };
    setBusy(true);
    setError(null);
    setFallback(null);
    if (!append) s.resetSources();
    setProgress({ message: t("research.search.queued", "正在排队…"), total: 0, source: null, newCount: 0, sourcesDone: [], sourcesPending: [] });
    try {
      const acc = await call<AsyncTaskAccepted>("POST", "/api/search/stream", body);
      abort.current.taskId = acc.task_id;
      const final = await awaitTask(acc.task_id, {
        signal: ctrl.signal,
        onEvent: (ev) => {
          const r = parseSearchData(ev.data);
          absorb({ ...r, kbIngestTaskId: null });
          setProgress((p) => ({
            message: ev.message || p?.message || "", total: r.progress.total ?? p?.total ?? 0, source: r.progress.source ?? null,
            newCount: r.progress.newCount ?? 0, sourcesDone: r.progress.sourcesDone ?? [], sourcesPending: r.progress.sourcesPending ?? [],
          }));
        },
      });
      const r = parseSearchData(final.data);
      absorb(r);
      if (r.kbIngestTaskId) void trackKb(r.kbIngestTaskId);
      void resolveKb(); // hits whose full text is already in the KB
      message.success(final.message || t("research.search.done", "检索完成，共 {{n}} 条", { n: useResearch.getState().sources.length }));
    } catch (e) {
      if (ctrl.signal.aborted) {
        message.info(t("research.search.cancelled", "检索已停止"));
        return;
      }
      // A dropped stream is not a failed search: retry once synchronously.
      setFallback(errorText(e));
      try {
        const r = await call<SearchResponse>("POST", "/api/search", body);
        absorb(parseSearchData(r as unknown as Record<string, unknown>));
        message.success(t("research.search.sync_done", "同步检索完成，共 {{n}} 条", { n: useResearch.getState().sources.length }));
      } catch (e2) {
        setError(e2);
      }
    } finally {
      setBusy(false);
      setProgress(null);
      abort.current = null;
    }
  };

  const stop = async () => {
    const a = abort.current;
    if (!a) return;
    a.ctrl.abort();
    if (a.taskId) await cancelTask(a.taskId).catch(() => undefined);
  };

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      const fd = new FormData();
      const many = files.length > 1;
      for (const f of files) fd.append(many ? "files" : "file", f);
      const acc = await call<AsyncTaskAccepted>("POST", many ? "/api/ingest/batch" : "/api/ingest", fd);
      const final = await awaitTask(acc.task_id, { timeoutMs: 900_000 });
      const data = (final.data ?? {}) as { evidence?: Evidence[]; warnings?: string[] };
      if (data.evidence?.length) s.addSources(data.evidence);
      for (const w of data.warnings ?? []) message.warning(w);
      message.success(t("research.upload.done", "已解析 {{n}} 条资料", { n: data.evidence?.length ?? 0 }));
    } catch (e) {
      message.error(t("research.upload.failed", "文件上传失败：{{e}}", { e: errorText(e) }));
    } finally {
      setUploading(false);
    }
  };

  const ingest = async (e: Evidence) => {
    const k = evidenceKey(e);
    patchRow(k, { busy: "ingest", error: null });
    try {
      const res = await call<IngestEvidenceResponse>("POST", "/api/kb/ingest-evidence", {
        identifier: e.identifier, title: e.title, url: e.url, url_alt: e.url_alt, source: e.source, project_id: projectId,
        assignee: e.assignee, pub_date: e.pub_date, snippet: e.snippet, oa_pdf_url: e.oa_pdf_url, is_oa: e.is_oa, relevance: e.relevance,
      });
      let sourceId = res.source_id ?? null;
      let st = res.status || (res.ok ? "indexed" : "failed");
      if (res.ok && res.task_id) {
        const final = await awaitTask(res.task_id, { timeoutMs: 900_000 });
        const d = (final.data ?? {}) as { source_id?: string; status?: string };
        sourceId = d.source_id ?? sourceId;
        st = d.status ?? "indexed";
      }
      patchRow(k, { status: st, sourceId, error: res.ok ? null : res.reason ?? null });
      if (res.ok) {
        message.success(st === "skipped" ? t("research.ingest.exists", "已在库 · {{id}}", { id: res.canonical_id || e.identifier })
          : t("research.ingest.done", "全文已入库 · {{id}}", { id: res.canonical_id || e.identifier }));
        void qc.invalidateQueries({ queryKey: ["kb", "sources"] });
      } else message.error(res.reason || t("research.ingest.failed", "入库全文失败"));
    } catch (err) {
      patchRow(k, { status: "failed", error: errorText(err) });
      message.error(errorText(err));
    } finally {
      patchRow(k, { busy: undefined });
    }
  };

  const extract = async (e: Evidence) => {
    const k = evidenceKey(e);
    const sourceId = sourceIdOf(k);
    patchRow(k, { busy: "draft" });
    try {
      const res = sourceId
        ? await call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/formulations/extract-embodiment-draft",
          { source_id: sourceId, domain, surechembl_hint: e.source === "surechembl" })
        : await call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/surechembl/extract-example-draft",
          { doc_id: e.identifier, title: e.title, assignee: e.assignee, pub_date: e.pub_date, url: e.url, domain });
      if (!res.ok || !res.draft) {
        message.warning((res.reason || t("research.emb.no_draft", "无法提取实施例草稿"))
          + (sourceId ? "" : t("research.emb.ingest_first", " · 提示：入库全文后可提取真实比重")));
        return;
      }
      setDraft(res.draft);
    } catch (err) {
      message.error(errorText(err));
    } finally {
      patchRow(k, { busy: undefined });
    }
  };

  const typeLabel: Record<SourceType, string> = {
    patents: t("research.type.patents", "专利"), surechembl: t("research.type.surechembl", "专利化学 SureChEMBL"),
    literature: t("research.type.literature", "文献"), internet: t("research.type.internet", "互联网"), notebooklm: "NotebookLM",
  };
  const pct = progress && progress.sourcesDone.length + progress.sourcesPending.length > 0
    ? Math.round((progress.sourcesDone.length / (progress.sourcesDone.length + progress.sourcesPending.length)) * 100) : undefined;

  return (
    <>
      <Card>
        <Input.Search size="large" enterButton={t("research.search.run", "开始检索")} loading={busy} value={s.query}
          onChange={(e) => s.setQuery(e.target.value)} onSearch={() => void run(false)}
          placeholder={t("research.search.placeholder", "检索外部专利、文献与网络，例如：水性环氧 锌粉 耐盐雾")} />
        <Space wrap style={{ marginTop: 12 }}>
          <Checkbox.Group value={s.sourceTypes} onChange={(v) => s.setSourceTypes(v as SourceType[])}
            options={SOURCE_TYPES.map((x) => {
              const st = status.data?.[x.id];
              return {
                value: x.id,
                label: (
                  <Tooltip title={st ? [st.reason, st.hint].filter(Boolean).join(" · ") || (st.available ? t("research.type.ok", "可用") : "") : undefined}>
                    <Badge status={sourceBadge(st)} text={typeLabel[x.id]} />
                  </Tooltip>
                ),
              };
            })} />
          {s.sourceTypes.includes("notebooklm") && (
            <Input style={{ width: 260 }} value={s.notebookId} onChange={(e) => s.setNotebookId(e.target.value)}
              placeholder={t("research.search.notebook", "NotebookLM Notebook ID")} />
          )}
          <Button disabled={busy || !s.query.trim()} onClick={() => void run(true)}>{t("research.search.append", "追加检索（保留现有资料）")}</Button>
          <Upload multiple showUploadList={false} beforeUpload={(_f, list) => {
            if (_f === list[0]) void upload(list as unknown as File[]);
            return false;
          }}>
            <Button icon={<UploadOutlined />} loading={uploading}>{t("research.upload", "上传本地文件")}</Button>
          </Upload>
        </Space>
      </Card>

      {progress && (
        <Card size="small" style={{ marginTop: 16 }} title={t("research.search.progress", "检索进度")}
          extra={<Button size="small" danger onClick={() => void stop()}>{t("research.stop", "停止")}</Button>}>
          <Progress percent={pct} status="active" showInfo={pct !== undefined} />
          <Typography.Text>{progress.message}</Typography.Text>
          <div style={{ marginTop: 8 }}>
            {progress.sourcesDone.map((x) => <Tag key={`d-${x}`} color="success">{x}</Tag>)}
            {progress.sourcesPending.map((x) => <Tag key={`p-${x}`} color="processing">{x}</Tag>)}
            {progress.total > 0 && <Tag>{t("research.search.total", "累计 {{n}} 条", { n: progress.total })}</Tag>}
          </div>
        </Card>
      )}

      <div style={{ marginTop: 16 }}>
        <ErrorAlert error={error} />
        {fallback && <Alert type="warning" showIcon closable style={{ marginBottom: 12 }}
          message={t("research.search.fallback", "流式检索中断（{{e}}），已改用同步检索", { e: fallback })} />}
        {s.usedSeedFallback && <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={t("research.search.seed", "部分结果来自离线示例语料（外部数据源不可用），请勿作为正式证据。")} />}
        {s.filterReport && s.filterReport.dropped > 0 && (
          <Alert type="info" showIcon closable style={{ marginBottom: 12 }}
            message={t("research.search.filtered", "质量过滤：保留 {{k}} 条，剔除 {{d}} 条", { k: s.filterReport.kept, d: s.filterReport.dropped })}
            description={<Space wrap>{Object.entries(s.filterReport.dropped_by_reason).map(([r, n]) => <Tag key={r}>{r} × {n}</Tag>)}</Space>} />
        )}
        {kb && (
          <Alert type={kb.active ? "info" : "success"} showIcon closable={!kb.active} onClose={() => setKb(null)} style={{ marginBottom: 12 }}
            message={kb.message}
            description={kb.progress && (
              <Space wrap>
                <Progress size="small" style={{ width: 200 }} percent={kb.progress.total ? Math.round((kb.progress.done / kb.progress.total) * 100) : 0} />
                <Tag color="success">{t("research.kb.indexed", "已入库 {{n}}", { n: kb.progress.indexed })}</Tag>
                {kb.progress.failed > 0 && <Tag color="error">{t("research.kb.failed", "失败 {{n}}", { n: kb.progress.failed })}</Tag>}
                <Tag>{kb.progress.done} / {kb.progress.total}</Tag>
              </Space>
            )} />
        )}
      </div>

      <ProTable<Evidence>
        rowKey={evidenceKey} search={false} dataSource={s.sources} options={false} size="small"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        headerTitle={t("research.sources.title", "资料（已选 {{s}} / {{n}}，用于问答与深度研究）", { s: s.selected.length, n: s.sources.length })}
        toolBarRender={() => [
          <Popconfirm key="clear" title={t("research.sources.clear_confirm", "清空全部资料？")} onConfirm={() => s.resetSources()} disabled={!s.sources.length}>
            <Button danger disabled={!s.sources.length}>{t("research.sources.clear", "清空")}</Button>
          </Popconfirm>,
        ]}
        rowSelection={{ selectedRowKeys: s.selected, onChange: (keys) => s.setSelected(keys as string[]) }}
        columns={[
          { title: t("research.sources.col_title", "标题"), dataIndex: "title", ellipsis: true, render: (_d, e) => {
            const target = citationTarget(e);
            const text = e.title || e.identifier;
            return target ? ("internal" in target ? <Link to={target.internal}>{text}</Link>
              : <a href={target.external} target="_blank" rel="noreferrer noopener">{text}</a>) : text;
          } },
          { title: t("research.sources.col_source", "来源"), dataIndex: "source", width: 140, render: (_d, e) => (
            <Space size={2} wrap>
              <Tag>{e.source}</Tag>
              {e.is_seed_corpus && <Tag color="warning">{t("research.sources.seed", "示例")}</Tag>}
              {e.is_oa && <Tag color="green">OA</Tag>}
            </Space>
          ) },
          { title: t("research.sources.col_id", "编号"), dataIndex: "identifier", width: 170, ellipsis: true, copyable: true },
          { title: t("research.sources.col_meta", "权利人 / 日期"), width: 160, ellipsis: true,
            render: (_d, e) => [e.assignee, e.pub_date, pageLabel(e)].filter(Boolean).join(" · ") || "—" },
          { title: t("research.sources.col_rel", "相关度"), dataIndex: "relevance", width: 80, sorter: (a, b) => a.relevance - b.relevance,
            render: (_d, e) => Number(e.relevance ?? 0).toFixed(2) },
          { title: t("research.sources.col_snippet", "摘要"), dataIndex: "snippet", ellipsis: true, hideInTable: false },
          { title: t("common.actions", "操作"), valueType: "option", width: 260, render: (_d, e) => {
            const k = evidenceKey(e);
            const r = rows[k];
            const sid = sourceIdOf(k);
            const inKb = r?.status === "indexed" || r?.status === "skipped" || (!!sid && r?.status !== "failed");
            const draftable = e.source === "surechembl" || !!sid;
            return [
              <Tooltip key="i" title={r?.error ?? undefined}>
                <Button size="small" type="link" icon={<CloudDownloadOutlined />} loading={r?.busy === "ingest"}
                  disabled={!e.identifier || inKb} onClick={() => void ingest(e)}>
                  {inKb ? t("research.ingest.in_kb", "已入库") : t("research.ingest.run", "入库全文")}
                </Button>
              </Tooltip>,
              sid && (
                <Link key="ft" to={`/knowledge/sources/${encodeURIComponent(sid)}`}>
                  <Button size="small" type="link" icon={<FileTextOutlined />}>{t("research.sources.fulltext", "全文")}</Button>
                </Link>
              ),
              draftable && (
                <Button key="d" size="small" type="link" icon={<ExperimentOutlined />} loading={r?.busy === "draft"} onClick={() => void extract(e)}>
                  {t("research.emb.extract", "实施例草稿")}
                </Button>
              ),
              <Button key="x" size="small" type="link" danger icon={<DeleteOutlined />} aria-label={t("common.delete", "删除")} onClick={() => s.removeSource(k)} />,
            ].filter(Boolean);
          } },
        ]}
        locale={{ emptyText: t("research.sources.empty", "先检索外部数据源或上传本地文件") }}
      />
      <EmbodimentDraftDrawer draft={draft} onClose={() => setDraft(null)} />
    </>
  );
}
