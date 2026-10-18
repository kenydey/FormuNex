import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Badge, Button, Flex, InputNumber, List, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { call } from "@/api/client";
import { awaitTask, type AsyncTaskAccepted } from "@/api/stream";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { groupDependencies } from "./logic";
import type { ChemToolsStatus, DependencyInfo, DependencyInstallResult, DependencyList, KbReindexResult, KbStats, RetentionPurgeResult } from "./types";

const DEPS_KEY = ["settings", "dependencies"] as const;
export const KB_STATS_KEY = ["settings", "kb-stats"] as const;

function extraLabel(t: TFunction, e: string): string {
  switch (e) {
    case "llm": return t("settings.deps.x_llm", "大模型供应商 · LLM");
    case "intel": return t("settings.deps.x_intel", "在线检索 · Retrieval");
    case "embedding": return t("settings.deps.x_embedding", "语义向量 RAG · Embedding");
    case "colbert": return t("settings.deps.x_colbert", "ColBERT 检索");
    case "crag": return t("settings.deps.x_crag", "联邦检索 · CRAG");
    case "science": return t("settings.deps.x_science", "科学计算 · Science");
    case "optimize": return t("settings.deps.x_optimize", "寻优器 · Optimize");
    case "bo": return t("settings.deps.x_bo", "高斯过程寻优 · BoTorch");
    case "pydoe": return t("settings.deps.x_pydoe", "经典 DOE · pyDOE");
    case "baybe": return t("settings.deps.x_baybe", "贝叶斯主动学习 · BayBE");
    case "color": return t("settings.deps.x_color", "色差 · Color");
    case "file_ingest": return t("settings.deps.x_file_ingest", "文件解析 · Ingest");
    case "parse_pro": return t("settings.deps.x_parse_pro", "版面感知解析 · Layout");
    case "export": return t("settings.deps.x_export", "导出 · Export");
    default: return e;
  }
}

function chemToolLabel(t: TFunction, k: string): string {
  switch (k) {
    case "name_to_smiles": return t("settings.chem.name_to_smiles", "名称→SMILES");
    case "name_to_cas": return t("settings.chem.name_to_cas", "名称→CAS");
    case "func_groups": return t("settings.chem.func_groups", "官能团识别");
    case "mol_similarity": return t("settings.chem.mol_similarity", "分子相似度");
    case "patent_check": return t("settings.chem.patent_check", "分子专利预筛");
    case "controlled_check": return t("settings.chem.controlled_check", "管制品筛查");
    case "explosive_check": return t("settings.chem.explosive_check", "爆炸性筛查");
    case "web_search": return t("settings.chem.web_search", "化学网络检索");
    default: return k;
  }
}

function ChemToolsCard() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["settings", "chem-tools"], queryFn: () => call<ChemToolsStatus>("GET", "/api/chemical/tools"), retry: false });
  if (!q.data) return null;
  const caps = Object.entries(q.data.capabilities ?? {});
  return (
    <ProCard bordered size="small" title={t("settings.chem.title", "化学工具网关 · {{ok}}/{{n}} 可用", { ok: caps.filter(([, c]) => c.available).length, n: caps.length })}
      extra={!q.data.enabled && <Tag color="warning">{t("settings.disabled", "已禁用")}</Tag>}>
      <Space wrap>
        {caps.map(([k, c]) => (
          <Tooltip key={k} title={c.hint ?? undefined}>
            <Tag color={c.available ? "success" : "default"}>{chemToolLabel(t, k)}</Tag>
          </Tooltip>
        ))}
      </Space>
    </ProCard>
  );
}

function KbStatsCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KB_STATS_KEY, queryFn: () => call<KbStats>("GET", "/api/kb/stats"), retry: false });
  const reindex = useMutation({
    mutationFn: () => call<KbReindexResult>("POST", "/api/kb/reindex", {}),
    onSuccess: (r) => {
      message.success(t("settings.kb.reindexed", "已重建 {{s}} 源 / {{c}} 切块", { s: r.reindexed_sources, c: r.reindexed_chunks }));
      void qc.invalidateQueries({ queryKey: KB_STATS_KEY });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const s = q.data;
  if (!s) return <ErrorAlert error={q.error} />;
  const g = s.quality_gate_drops;
  const drops = [
    g?.retrieval?.blocked_domain ? t("settings.kb.drop_r_domain", "检索拦域名 {{n}}", { n: g.retrieval.blocked_domain }) : null,
    g?.retrieval?.garbage_snippet ? t("settings.kb.drop_r_garbage", "检索垃圾 {{n}}", { n: g.retrieval.garbage_snippet }) : null,
    g?.retrieval?.wiki_track ? t("settings.kb.drop_r_wiki", "检索 wiki {{n}}", { n: g.retrieval.wiki_track }) : null,
    g?.ingest?.blocked_domain ? t("settings.kb.drop_i_domain", "入库拦域名 {{n}}", { n: g.ingest.blocked_domain }) : null,
    g?.ingest?.garbage_snippet ? t("settings.kb.drop_i_garbage", "入库垃圾 {{n}}", { n: g.ingest.garbage_snippet }) : null,
  ].filter(Boolean);
  const vectorColor = s.vector_mode === "semantic" ? "success" : s.vector_mode === "degraded" ? "warning" : "default";
  return (
    <ProCard bordered size="small" title={t("settings.kb.title", "持久知识库")}
      extra={s.enabled ? (
        <Button size="small" loading={reindex.isPending} onClick={() => reindex.mutate()} title={t("settings.kb.reindex_tip", "对已存储的全部文档重建切块与向量索引")}>
          {t("settings.kb.reindex", "重建索引")}
        </Button>
      ) : <Tag color="warning">{t("settings.disabled", "已禁用")}</Tag>}>
      <Space wrap>
        <Tag>{t("settings.kb.sources", "文档 {{n}}", { n: s.sources })}</Tag>
        {(s.sources_archived ?? 0) > 0 && <Tag>{t("settings.kb.active_archived", "活跃 {{a}} · 归档 {{b}}", { a: s.sources_active ?? "?", b: s.sources_archived })}</Tag>}
        <Tag>{t("settings.kb.chunks", "切块 {{n}}", { n: s.chunks })}</Tag>
        {s.embedding_model && (
          <Tooltip title={[s.reindex_hint || t("settings.kb.reindex_hint", "换模型后必须重建索引"),
            s.embedding_catalog?.length ? s.embedding_catalog.map((c) => c.id).join(", ") : ""].filter(Boolean).join(" · ")}>
            <Tag color="blue">{t("settings.kb.model", "模型 {{m}}", { m: s.embedding_model.split("/").pop() })}</Tag>
          </Tooltip>
        )}
        {/* Keyed on vector_mode: an importable library says nothing about whether any chunk carries a vector. */}
        <Tooltip title={s.vector_hint || (s.vector_mode === "semantic" ? s.rag_backend : undefined)}>
          <Tag color={vectorColor}>{t("settings.kb.vectors", "向量 {{a}}/{{b}}", { a: s.embedded_chunks, b: s.chunks })}</Tag>
        </Tooltip>
        {typeof s.scan_pressure === "number" && (s.scan_limit ?? 0) > 0 && (
          <Tooltip title={t("settings.kb.scan_tip", "活跃切块 / 扫描上限 = {{a}}/{{b}}", { a: s.chunks_active ?? "?", b: s.scan_limit })}>
            <Tag color={s.scan_near_cap ? "warning" : "default"}>{t("settings.kb.scan", "扫描 {{p}}%", { p: (s.scan_pressure * 100).toFixed(0) })}</Tag>
          </Tooltip>
        )}
        {s.suppliers_json_dual_write === false && <Tag color="purple">{t("settings.kb.suppliers_single", "供应商 JSON 单写")}</Tag>}
        {(s.products ?? 0) > 0 && <Tag color="gold">{t("settings.kb.products", "商业产品 {{n}}", { n: s.products })}</Tag>}
        {Object.entries(s.sources_by_kind ?? {}).map(([k, n]) => <Tag key={k}>{k} ×{n}</Tag>)}
      </Space>
      {drops.length > 0 && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          {t("settings.kb.gate_drops", "门禁丢弃（进程内累计，重启清零）")}：{drops.join(" · ")}
        </Typography.Paragraph>
      )}
    </ProCard>
  );
}

/** Purge soft-archived sources past N days: always a dry run first, then an explicit confirmation. */
function RetentionPurgeCard() {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const qc = useQueryClient();
  const stats = useQuery({ queryKey: KB_STATS_KEY, queryFn: () => call<KbStats>("GET", "/api/kb/stats"), retry: false });
  const [days, setDays] = useState(30);
  const [dry, setDry] = useState<RetentionPurgeResult | null>(null);
  const run = useMutation({
    mutationFn: (confirm: boolean) => call<RetentionPurgeResult>("POST", "/api/kb/retention/purge",
      { days, dry_run: !confirm, confirm, limit: 200 }),
    onSuccess: (r, confirm) => {
      if (!confirm) return setDry(r);
      setDry(null);
      if (r.ok) message.success(t("settings.purge.done", "已清理 {{a}} / {{b}} 条", { a: r.purged_count, b: r.candidate_count }));
      else message.warning(t("settings.purge.partial", "清理部分失败：成功 {{a}}，错误 {{b}}", { a: r.purged_count, b: r.errors.length }));
      void qc.invalidateQueries({ queryKey: KB_STATS_KEY });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const near = !!stats.data?.scan_near_cap;
  return (
    <ProCard bordered size="small" title={t("settings.purge.title", "归档清理")}
      extra={near ? <Tag color="warning">{t("settings.purge.near_cap", "扫描近上限")}</Tag> : null}
      subTitle={t("settings.purge.sub", "仅删除已软归档且超龄的源；先预览，不会自动 TTL")}>
      <Space wrap>
        <Space.Compact>
          <Button disabled>{t("settings.purge.older", "超过")}</Button>
          <InputNumber min={1} max={3650} value={days} onChange={(v) => { setDays(v ?? 1); setDry(null); }} />
          <Button disabled>{t("settings.purge.days", "天")}</Button>
        </Space.Compact>
        <Button loading={run.isPending && run.variables === false} onClick={() => run.mutate(false)}>{t("settings.preview", "预览")}</Button>
        <Button danger disabled={!dry || dry.candidate_count === 0} loading={run.isPending && run.variables === true}
          onClick={() => modal.confirm({
            title: t("settings.purge.confirm", "确认物理删除 {{n}} 条已归档且超过 {{d}} 天的知识库文档？", { n: dry?.candidate_count ?? 0, d: days }),
            content: t("settings.purge.confirm_body", "此操作不可撤销（切块/提及一并清理）。"),
            okButtonProps: { danger: true },
            onOk: () => run.mutate(true),
          })}>
          {t("settings.purge.run", "确认清理")}
        </Button>
      </Space>
      {dry && (
        <>
          <Typography.Paragraph style={{ marginTop: 8 }}>
            {t("settings.purge.preview", "预览：{{n}} 条归档源超过 {{d}} 天", { n: dry.candidate_count, d: dry.days })}
          </Typography.Paragraph>
          <List size="small" rowKey="source_id" dataSource={dry.candidates.slice(0, 20)} renderItem={(c) => (
            <List.Item>{(c.title || c.source_id).slice(0, 80)}{c.archived_at ? ` · ${c.archived_at.slice(0, 10)}` : ""}</List.Item>
          )} footer={dry.candidates.length > 20 ? t("settings.purge.more", "…另有 {{n}} 条", { n: dry.candidates.length - 20 }) : undefined} />
        </>
      )}
    </ProCard>
  );
}

/** Optional pip dependencies: status, install selected / online core, upgrade — runs as a background task. */
export function DependenciesTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: DEPS_KEY, queryFn: () => call<DependencyList>("GET", "/api/dependencies") });
  const [selected, setSelected] = useState<string[]>([]);
  const [progress, setProgress] = useState("");
  const [result, setResult] = useState<DependencyInstallResult | null>(null);
  const [showLog, setShowLog] = useState(false);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const installEnabled = q.data?.install_enabled !== false;
  const coreMissing = q.data?.online_core_missing ?? [];
  const deps = q.data?.dependencies ?? [];
  const rows = groupDependencies(deps).flatMap((g) => g.items);

  const install = useMutation({
    mutationFn: async ({ names, upgrade }: { names: string[]; upgrade: boolean }) => {
      setResult(null);
      setShowLog(false);
      setProgress(t("settings.deps.running", "{{op}}中：{{n}} …", { op: upgrade ? t("settings.deps.op_upgrade", "更新") : t("settings.deps.op_install", "安装"), n: names.join(", ") }));
      const acc = await call<AsyncTaskAccepted>("POST", "/api/dependencies/install", { names, upgrade });
      abort.current = new AbortController();
      const final = await awaitTask(acc.task_id, { signal: abort.current.signal, onEvent: (ev) => ev.message && setProgress(ev.message) });
      return (final.data as unknown as DependencyInstallResult | undefined) ?? { ok: false, summary: final.message };
    },
    onSuccess: (r) => {
      setResult(r);
      setSelected([]);
    },
    onError: (e) => setResult({ ok: false, summary: errorText(e) }),
    onSettled: () => {
      setProgress("");
      void qc.invalidateQueries({ queryKey: DEPS_KEY });
    },
  });

  const busy = install.isPending;
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.deps.intro", "检索软件可选依赖的安装状态，勾选后安装、一键补齐在线模式所需依赖或更新到最新版。安装在后端机器上执行（pip），完成后需重启后端服务生效。")}
      </Typography.Paragraph>
      {!installEnabled && (
        <Alert type="warning" showIcon message={t("settings.deps.disabled", "服务端已禁用 pip 安装（生产默认关闭）。如需启用：设置 FORMUNEX_DEPS_INSTALL_ENABLED=true 并重启后端。")} />
      )}
      <ErrorAlert error={q.error} />
      <ChemToolsCard />
      <KbStatsCard />
      <RetentionPurgeCard />
      {busy && <Alert type="info" showIcon message={progress} />}
      {result && !busy && (
        <Alert type={result.ok ? "success" : "error"} showIcon message={result.summary}
          description={(result.stdout || result.stderr) && (
            <>
              <a onClick={() => setShowLog((v) => !v)}>{showLog ? t("settings.deps.hide_log", "隐藏日志") : t("settings.deps.show_log", "查看 pip 日志")}</a>
              {showLog && <pre style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 12 }}>{`${result.stdout ?? ""}\n${result.stderr ?? ""}`}</pre>}
            </>
          )} />
      )}
      <ProCard bordered size="small" title={t("settings.deps.catalog", "可选依赖")}
        subTitle={t("settings.deps.count", "已安装 {{a}} / {{b}} 项", { a: deps.filter((d) => d.installed).length, b: deps.length })}
        extra={
          <Space wrap>
            <Button type="primary" disabled={busy || !installEnabled || coreMissing.length === 0}
              onClick={() => install.mutate({ names: coreMissing, upgrade: false })} title={coreMissing.join(", ")}>
              {coreMissing.length === 0 ? t("settings.deps.core_ready", "在线核心已就绪") : t("settings.deps.install_core", "一键安装在线核心（{{n}}）", { n: coreMissing.length })}
            </Button>
            <Button disabled={busy || !installEnabled || selected.length === 0} onClick={() => install.mutate({ names: selected, upgrade: false })}>
              {t("settings.deps.install_selected", "安装选中（{{n}}）", { n: selected.length })}
            </Button>
            <Button disabled={busy || !installEnabled || selected.length === 0} onClick={() => install.mutate({ names: selected, upgrade: true })}>
              {t("settings.deps.upgrade_selected", "更新选中到最新版")}
            </Button>
            <Button loading={q.isFetching} disabled={busy} onClick={() => void q.refetch()}>{t("settings.refresh", "刷新")}</Button>
          </Space>
        }>
        <Table<DependencyInfo>
          rowKey="pip_name" size="small" loading={q.isLoading} dataSource={rows} pagination={false}
          rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys as string[]) }}
          columns={[
            { title: t("settings.deps.group", "分组"), dataIndex: "extra", width: 200, render: (e: string) => extraLabel(t, e),
              onCell: (r, i) => {
                const idx = i ?? 0;
                if (idx > 0 && rows[idx - 1]?.extra === r.extra) return { rowSpan: 0 };
                let n = 1;
                while (rows[idx + n]?.extra === r.extra) n++;
                return { rowSpan: n };
              } },
            { title: t("settings.deps.package", "包"), dataIndex: "pip_name", render: (n: string) => <Typography.Text code>{n}</Typography.Text> },
            { title: t("settings.deps.status", "状态"), width: 140, render: (_v, d) => d.installed
              ? <Badge status="success" text={d.version ? `v${d.version}` : t("settings.deps.installed", "已安装")} />
              : <Badge status="default" text={t("settings.deps.missing", "未安装")} /> },
            { title: t("settings.deps.enables", "启用能力"), dataIndex: "enables" },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          {t("settings.deps.slow_hint", "安装大模型库（torch/botorch/sentence-transformers）可能耗时数分钟。")}
        </Typography.Paragraph>
      </ProCard>
    </Flex>
  );
}
