import { Alert, App, Button, Drawer, Form, Input, Progress, Radio, Space, Table, Tabs, Tag, Typography, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, cancelTask, type AsyncTaskAccepted, type TaskProgressEvent } from "@/api/stream";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { EmbodimentDraftDrawer } from "./EmbodimentDraftDrawer";
import { UPLOAD_ACCEPT, UPLOAD_BATCH_MAX, canIngestFulltext, chunk, mergeEvidence, parseSearchStreamData, searchableTypes } from "./logic";
import { SourceTypePicker, useSourceTypeLabel } from "./SourceTypePicker";
import type { EmbodimentDraft, Evidence, IngestEvidenceResult, IngestResponse, SearchSourceType, SourceStatus } from "./types";

const SEARCH_TYPES: SearchSourceType[] = ["patents", "surechembl", "literature", "internet", "notebooklm"];

function Warnings({ warnings }: { warnings: string[] }) {
  const { t } = useTranslation();
  if (!warnings.length) return null;
  return <Alert type="warning" showIcon message={t("library.add.warnings", "解析提示")}
    description={<ul style={{ margin: 0, paddingLeft: 18 }}>{warnings.slice(0, 8).map((w) => <li key={w}>{w}</li>)}</ul>} />;
}

/** Upload files: queued parse tasks (202 + task id), batches of ≤ 20 files, with progress. */
function UploadTab({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [ev, setEv] = useState<TaskProgressEvent | null>(null);
  const [batch, setBatch] = useState<[number, number] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const projectId = useUi((s) => s.projectId);
  const [scope, setScope] = useState<"project" | "global">(projectId ? "project" : "global");
  const scopedProject = scope === "project" ? projectId : null;
  const run = useMutation({
    mutationFn: async () => {
      const raws = files.map((f) => f.originFileObj).filter((f): f is NonNullable<typeof f> => !!f);
      const groups = chunk(raws, UPLOAD_BATCH_MAX);
      const warn: string[] = [];
      let total = 0;
      for (let i = 0; i < groups.length; i++) {
        setBatch([i + 1, groups.length]);
        const fd = new FormData();
        const single = groups[i].length === 1;
        for (const f of groups[i]) fd.append(single ? "file" : "files", f);
        if (scopedProject) fd.append("project_id", scopedProject);
        const acc = await call<AsyncTaskAccepted>("POST", single ? "/api/ingest" : "/api/ingest/batch", fd);
        const final = await awaitTask(acc.task_id, { onEvent: setEv, timeoutMs: 900_000 });
        const data = (final.data ?? {}) as Partial<IngestResponse>;
        warn.push(...(data.warnings ?? []));
        total += data.evidence?.length ?? 0;
      }
      return { warn, total };
    },
    onSuccess: ({ warn, total }) => {
      setWarnings(warn);
      setFiles([]);
      message.success(t("library.add.uploaded", "上传解析完成：{{n}} 条资料", { n: total }));
      onDone();
    },
    onSettled: () => setBatch(null),
  });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap>
        <Typography.Text>{t("library.add.scope", "入库范围")}</Typography.Text>
        <Radio.Group optionType="button" size="small" value={projectId ? scope : "global"} disabled={run.isPending}
          onChange={(e) => setScope(e.target.value as "project" | "global")} options={[
            { value: "global", label: t("library.add.scope_global", "全局") },
            { value: "project", label: t("library.add.scope_project", "当前项目"), disabled: !projectId },
          ]} />
        {!projectId && <Typography.Text type="secondary">{t("library.add.scope_no_project", "未选择项目，上传将进入全局知识库")}</Typography.Text>}
      </Space>
      <Upload.Dragger multiple accept={UPLOAD_ACCEPT} fileList={files} beforeUpload={() => false}
        onChange={({ fileList }) => setFiles(fileList)} disabled={run.isPending}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">{t("library.add.drop", "点击或拖拽文件到此处")}</p>
        <p className="ant-upload-hint">{t("library.add.drop_hint", "PDF / Word / Excel / PPT / HTML / TXT / MD / CSV / 图片；每批最多 20 个")}</p>
      </Upload.Dragger>
      <Button type="primary" disabled={!files.length} loading={run.isPending} onClick={() => run.mutate()}>
        {t("library.add.upload", "上传并解析（{{n}} 个文件）", { n: files.length })}
      </Button>
      {run.isPending && (
        <div>
          {batch && batch[1] > 1 && <Typography.Text type="secondary">{t("library.add.batch", "第 {{i}} / {{n}} 批", { i: batch[0], n: batch[1] })} · </Typography.Text>}
          <Typography.Text type="secondary">{ev?.message || t("library.add.queued", "排队中…")}</Typography.Text>
          <Progress percent={Math.round(Math.min(1, ev?.progress ?? 0) * 100)} size="small" />
        </div>
      )}
      <ErrorAlert error={run.error} />
      <Warnings warnings={warnings} />
    </Space>
  );
}

/** URL / pasted text / identifier imports: synchronous endpoints answering with the ingested evidence. */
function SimpleImport({ kind, onDone }: { kind: "url" | "text" | "id"; onDone: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [warnings, setWarnings] = useState<string[]>([]);
  const docType = Form.useWatch("doc_type", form) as string | undefined;
  const run = useMutation({
    mutationFn: (v: Record<string, string>) => {
      if (kind === "url") return call<IngestResponse>("POST", "/api/ingest/url", { url: v.url.trim() });
      if (kind === "text") return call<IngestResponse>("POST", "/api/ingest/text", { text: v.text.trim(), title: v.title?.trim() || "Pasted text" });
      return call<IngestResponse>("POST", "/api/ingest/task", { doc_type: v.doc_type, identifier: v.identifier.trim() });
    },
    onSuccess: (r) => {
      setWarnings(r.warnings ?? []);
      form.resetFields(kind === "id" ? ["identifier"] : undefined);
      message.success(t("library.add.imported", "已入库：{{n}} 条资料", { n: r.evidence?.length ?? r.total ?? 0 }));
      onDone();
    },
  });
  const placeholders: Record<string, string> = {
    paper: t("library.add.ph_paper", "DOI 或 arXiv id（如 10.1000/xyz123 / 2301.00001）"),
    patent: t("library.add.ph_patent", "专利号（如 US10435532B2 / CN110467878A）"),
    web: t("library.add.ph_web", "网页 URL"),
  };
  return (
    <Form form={form} layout="vertical" onFinish={(v) => run.mutate(v)} initialValues={{ doc_type: "paper" }}>
      {kind === "url" && (
        <Form.Item name="url" label={t("library.add.url", "网页 URL")} rules={[{ required: true, whitespace: true }]}>
          <Input placeholder="https://…" />
        </Form.Item>
      )}
      {kind === "text" && (
        <>
          <Form.Item name="title" label={t("library.add.text_title", "标题（可选）")}><Input /></Form.Item>
          <Form.Item name="text" label={t("library.add.text", "文本内容")} rules={[{ required: true, whitespace: true }]}>
            <Input.TextArea rows={10} />
          </Form.Item>
        </>
      )}
      {kind === "id" && (
        <>
          <Form.Item name="doc_type">
            <Radio.Group optionType="button" options={[
              { value: "paper", label: t("library.add.paper", "文献 DOI / arXiv") },
              { value: "patent", label: t("library.add.patent", "专利号") },
              { value: "web", label: t("library.add.web", "网页") },
            ]} />
          </Form.Item>
          <Form.Item name="identifier" rules={[{ required: true, whitespace: true }]}
            extra={t("library.add.id_hint", "自动获取全文并入库（OA 优先，多源兜底）；成功后即可用于检索与问答")}>
            <Input placeholder={placeholders[docType ?? "paper"]} />
          </Form.Item>
        </>
      )}
      <Button type="primary" htmlType="submit" loading={run.isPending}>{t("library.add.submit", "确定导入")}</Button>
      <div style={{ marginTop: 12 }}><ErrorAlert error={run.error} /><Warnings warnings={warnings} /></div>
    </Form>
  );
}

type RowState = { status: string; source_id?: string | null; error?: string | null };

/** Federated web search (patents / literature / internet / NotebookLM); hits can be ingested into the KB one by one. */
function WebSearchTab({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const label = useSourceTypeLabel();
  const projectId = useUi((s) => s.projectId);
  const [query, setQuery] = useState("");
  const [types, setTypes] = useState<SearchSourceType[]>(["patents", "surechembl", "literature", "internet"]);
  const [hits, setHits] = useState<Evidence[]>([]);
  const [ev, setEv] = useState<TaskProgressEvent | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<EmbodimentDraft | null>(null);
  const [seed, setSeed] = useState(false);
  const task = useRef<{ id?: string; ctrl?: AbortController }>({});
  const status = useQuery({ queryKey: ["search", "status"], queryFn: () => call<Record<string, SourceStatus>>("GET", "/api/search/status") });
  const project = useQuery({
    queryKey: ["project", projectId], enabled: !!projectId && types.includes("notebooklm"),
    queryFn: () => call<{ workspace?: { notebooklm_notebook_id?: string | null } }>("GET", `/api/projects/${projectId}`),
  });
  const notebookId = (project.data?.workspace?.notebooklm_notebook_id ?? "").trim();
  const needNotebook = types.includes("notebooklm") && !notebookId;

  const search = useMutation({
    mutationFn: async () => {
      setHits([]);
      setSeed(false);
      const ctrl = new AbortController();
      const sel = searchableTypes(types);
      const acc = await call<AsyncTaskAccepted>("POST", "/api/search/stream", {
        query: query.trim(), source_types: sel, total_limit: 300,
        notebooklm_notebook_id: sel.includes("notebooklm") ? notebookId || undefined : undefined,
      });
      task.current = { id: acc.task_id, ctrl };
      const onData = (d: Record<string, unknown> | undefined) => {
        const p = parseSearchStreamData(d);
        if (p.usedSeedFallback) setSeed(true);
        if (p.evidence.length) setHits((prev) => mergeEvidence(prev, p.evidence));
      };
      const final = await awaitTask(acc.task_id, { signal: ctrl.signal, onEvent: (e) => { setEv(e); onData(e.data); } });
      onData(final.data);
    },
    onSettled: () => { task.current = {}; },
  });
  const stop = async () => {
    const { id, ctrl } = task.current;
    ctrl?.abort();
    if (id) await cancelTask(id).catch(() => undefined);
  };

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const ingest = (e: Evidence) => act(`ft:${e.identifier}`, async () => {
    setRows((r) => ({ ...r, [e.identifier]: { status: "fetching" } }));
    try {
      const res = await call<IngestEvidenceResult>("POST", "/api/kb/ingest-evidence", {
        identifier: e.identifier, title: e.title, url: e.url, url_alt: e.url_alt, source: e.source, project_id: projectId,
        assignee: e.assignee, pub_date: e.pub_date, snippet: e.snippet, oa_pdf_url: e.oa_pdf_url, is_oa: e.is_oa, relevance: e.relevance,
      });
      const st = res.status || (res.ok ? "indexed" : "failed");
      setRows((r) => ({ ...r, [e.identifier]: { status: st, source_id: res.source_id, error: res.reason } }));
      if (res.ok) {
        message.success(st === "skipped" ? t("library.search.already", "已在库 · {{id}}", { id: res.canonical_id || e.identifier })
          : t("library.search.ingested", "全文已入库 · {{id}}", { id: res.canonical_id || e.identifier }));
        onDone();
      } else message.warning(res.reason || t("library.search.ingest_failed", "入库全文失败"));
    } catch (err) {
      setRows((r) => ({ ...r, [e.identifier]: { status: "failed", error: errorText(err) } }));
      throw err;
    }
  });
  const ingestKg = (e: Evidence) => act(`kg:${e.identifier}`, async () => {
    const res = await call<{ entities: number; links: number; link_type: string }>("POST", "/api/surechembl/kg/ingest-document", {
      doc_id: e.identifier, title: e.title, assignee: e.assignee, pub_date: e.pub_date, url: e.url, fetch_chemistry: true,
    });
    message.success(t("library.search.kg_done", "图谱已更新 · {{id}}：{{e}} 实体 / {{l}} 边", { id: e.identifier, e: res.entities, l: res.links }));
  });
  const extract = (e: Evidence) => act(`draft:${e.identifier}`, async () => {
    const sid = rows[e.identifier]?.source_id;
    const res = sid
      ? await call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/formulations/extract-embodiment-draft", { source_id: sid, surechembl_hint: true })
      : await call<{ ok: boolean; draft?: EmbodimentDraft; reason?: string }>("POST", "/api/surechembl/extract-example-draft", {
        doc_id: e.identifier, title: e.title, assignee: e.assignee, pub_date: e.pub_date, url: e.url });
    if (!res.ok || !res.draft) message.warning(res.reason || t("library.search.no_draft", "无法提取实施例草稿"));
    else setDraft(res.draft);
  });

  const ROW_COLOR: Record<string, string> = { fetching: "processing", indexing: "processing", indexed: "success", skipped: "success", failed: "error" };
  const rowLabel = (s: string) => ({
    fetching: t("library.search.st_fetching", "获取全文"), indexing: t("library.search.st_indexing", "入库中"), indexed: t("library.search.st_indexed", "已入库"),
    skipped: t("library.search.st_skipped", "已在库"), failed: t("library.search.st_failed", "入库失败"),
  }[s] ?? s);

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Input.Search value={query} onChange={(e) => setQuery(e.target.value)} enterButton={t("library.search.run", "检索")}
        placeholder={t("library.search.placeholder", "输入检索关键词，例如：水性环氧 防腐 盐雾")} loading={search.isPending}
        onSearch={() => { if (query.trim() && searchableTypes(types).length && !needNotebook) search.mutate(); }} />
      <SourceTypePicker value={types} onChange={setTypes} status={status.data} types={SEARCH_TYPES} />
      {needNotebook && <Alert type="warning" showIcon message={<span>{t("library.search.need_notebook", "所选项目未绑定 NotebookLM Notebook ID")} <Link to="/knowledge/notebooklm">{t("library.search.bind", "去绑定")}</Link></span>} />}
      {search.isPending && (
        <Space>
          <Typography.Text type="secondary">{ev?.message || t("library.add.queued", "排队中…")} · {t("library.search.count", "{{n}} 条", { n: hits.length })}</Typography.Text>
          <Button size="small" danger onClick={() => void stop()}>{t("library.search.stop", "停止")}</Button>
        </Space>
      )}
      {!search.isPending && <ErrorAlert error={search.error} />}
      {seed && <Alert type="info" showIcon message={t("library.search.seed", "部分来源离线，结果含示例语料（不可入库）")} />}
      <Table<Evidence> size="small" rowKey={(e) => e.identifier || e.title} dataSource={hits} pagination={{ pageSize: 20 }}
        columns={[
          { title: t("library.search.title", "标题"), render: (_v, e) => (
            <Space direction="vertical" size={0}>
              <span>{e.is_seed_corpus && <Tag color="gold">{t("library.search.sample", "示例")}</Tag>}
                {e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.title}</a> : e.title}</span>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{e.identifier}{e.assignee ? ` · ${e.assignee}` : ""}{e.pub_date ? ` · ${e.pub_date}` : ""}</Typography.Text>
            </Space>) },
          { title: t("library.search.source", "来源"), dataIndex: "source", width: 120, render: (v: string) => <Tag>{label(v)}</Tag> },
          { title: t("library.search.kb", "入库"), width: 100, render: (_v, e) => {
            const r = rows[e.identifier];
            return r ? <Tag color={ROW_COLOR[r.status] ?? "default"} title={r.error ?? undefined}>{rowLabel(r.status)}</Tag> : null;
          } },
          { title: t("common.actions", "操作"), width: 260, render: (_v, e) => (
            <Space size={4} wrap>
              {canIngestFulltext(e) && (
                <Button size="small" loading={busy === `ft:${e.identifier}`} onClick={() => void ingest(e)}>
                  {rows[e.identifier]?.source_id ? t("library.search.reingest", "再入库全文") : t("library.search.ingest", "入库全文")}
                </Button>
              )}
              {e.source === "surechembl" && e.identifier && (
                <>
                  <Button size="small" loading={busy === `kg:${e.identifier}`} onClick={() => void ingestKg(e)}>{t("library.search.kg", "入库图谱")}</Button>
                  <Button size="small" loading={busy === `draft:${e.identifier}`} onClick={() => void extract(e)}>{t("library.search.draft", "提取实施例草稿")}</Button>
                </>
              )}
              {rows[e.identifier]?.source_id && <Link to={`/knowledge/sources/${rows[e.identifier]!.source_id}`}>{t("library.search.open", "查看")}</Link>}
            </Space>) },
        ]} />
      <EmbodimentDraftDrawer draft={draft} onClose={() => setDraft(null)} />
    </Space>
  );
}

/** "Add sources": file upload, web page, pasted text, DOI / patent number, and federated web search. */
export function AddSourceDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["kb", "sources"] });
  return (
    <Drawer open={open} onClose={onClose} width={960} title={t("library.add.title", "添加数据源")} destroyOnHidden>
      <Tabs items={[
        { key: "upload", label: t("library.add.tab_upload", "上传文件"), children: <UploadTab onDone={refresh} /> },
        { key: "id", label: t("library.add.tab_id", "按标识符"), children: <SimpleImport kind="id" onDone={refresh} /> },
        { key: "url", label: t("library.add.tab_url", "网页"), children: <SimpleImport kind="url" onDone={refresh} /> },
        { key: "text", label: t("library.add.tab_text", "粘贴文本"), children: <SimpleImport kind="text" onDone={refresh} /> },
        { key: "search", label: t("library.add.tab_search", "联网检索"), children: <WebSearchTab onDone={refresh} /> },
      ]} />
    </Drawer>
  );
}
