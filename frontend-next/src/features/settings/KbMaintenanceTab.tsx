import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Flex, Input, InputNumber, List, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, cancelTask, getTask } from "@/api/stream";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { neo4jSummary } from "./logic";
import type {
  Evidence, KbChunk, KbIntegrity, KbProduct, KgCalibration, KgRebuildReport, KgRetrieve, KgStats, Neo4jCompound,
  Neo4jFormulation, Neo4jHit, Neo4jRawStats,
} from "./types";

const KG_STATS_KEY = ["settings", "kg-stats"] as const;
const NEO_KEY = ["settings", "neo4j"] as const;

interface Hit { key: string; title: string; snippet: string; score?: number }

function HitList({ hits }: { hits: Hit[] | null }) {
  const { t } = useTranslation();
  if (hits == null) return null;
  return (
    <List size="small" rowKey="key" dataSource={hits} locale={{ emptyText: t("settings.kbm.no_hits", "无命中（或 KB 未启用）") }}
      style={{ maxHeight: 280, overflow: "auto" }}
      renderItem={(h) => (
        <List.Item extra={h.score != null ? <Typography.Text type="secondary">{h.score.toFixed(3)}</Typography.Text> : null}>
          <List.Item.Meta title={h.title} description={<Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0 }}>{h.snippet}</Typography.Paragraph>} />
        </List.Item>
      )} />
  );
}

const fromEvidence = (ev: Evidence[]): Hit[] =>
  ev.map((e, i) => ({ key: `${e.identifier || e.title}-${i}`, title: e.title || e.identifier || e.source, snippet: e.snippet || "", score: e.relevance }));

/** KG health: stats, integrity scan, calibration snapshot, full rebuild and the async relation re-extraction task. */
function KgHealthCard() {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const qc = useQueryClient();
  const stats = useQuery({ queryKey: KG_STATS_KEY, queryFn: () => call<KgStats>("GET", "/api/kg/stats"), retry: false });
  const integrity = useMutation({ mutationFn: () => call<KbIntegrity>("GET", "/api/kb/integrity"), onError: (e) => message.error(errorText(e)) });
  const calibration = useMutation({ mutationFn: () => call<KgCalibration>("GET", "/api/kg/calibration"), onError: (e) => message.error(errorText(e)) });
  const rebuild = useMutation({
    mutationFn: () => call<KgRebuildReport>("POST", "/api/kg/rebuild", {}),
    onSuccess: (r) => {
      const failed = r.failed_sources ?? 0;
      const text = t("settings.kbm.rebuilt", "图谱重建完成：{{s}} 源 / {{e}} 实体 / {{m}} 提及 / {{l}} 链接",
        { s: r.linked_sources, e: r.entities_upserted, m: r.mentions_upserted, l: r.links_created });
      if (failed > 0) message.warning(`${text} · ${t("settings.kbm.rebuild_failed_n", "{{n}} 个源失败，详见后端日志", { n: failed })}`);
      else message.success(text);
      void qc.invalidateQueries({ queryKey: KG_STATS_KEY });
    },
    onError: (e) => message.error(errorText(e)),
  });

  // Relation re-extraction runs as a background task (LLM, slow): stream progress, allow cancel.
  const [rel, setRel] = useState<{ taskId: string | null; status: string; running: boolean }>({ taskId: null, status: "", running: false });
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  async function runRelations() {
    const ctrl = new AbortController();
    abort.current = ctrl;
    setRel({ taskId: null, status: "queued", running: true });
    let taskId: string | null = null;
    try {
      const r = await call<{ task_id: string }>("POST", "/api/kg/relations/rebuild", { source_id: null, limit: 50 });
      taskId = r.task_id;
      setRel({ taskId, status: "queued", running: true });
      const final = await awaitTask(r.task_id, { signal: ctrl.signal, timeoutMs: 5 * 60 * 1000,
        onEvent: (ev) => setRel({ taskId, status: `${ev.status}: ${ev.message ?? ev.stage ?? ""}`.slice(0, 120), running: true }) });
      setRel({ taskId, status: `completed: ${final.message ?? ""}`.slice(0, 120), running: false });
      message.success(t("settings.kbm.relations_done", "关系重建完成，已刷新统计"));
      void qc.invalidateQueries({ queryKey: KG_STATS_KEY });
    } catch (e) {
      const cancelled = ctrl.signal.aborted;
      setRel({ taskId, status: cancelled ? "cancelled" : "failed", running: false });
      if (!cancelled) message.error(errorText(e));
    } finally {
      abort.current = null;
    }
  }
  async function cancelRelations() {
    abort.current?.abort();
    if (rel.taskId) await cancelTask(rel.taskId).catch(() => undefined);
  }
  async function refreshRelations() {
    if (!rel.taskId) return;
    try {
      const s = await getTask(rel.taskId);
      setRel((r) => ({ ...r, status: `${s.state}: ${s.message ?? ""}`.slice(0, 120) }));
      if (s.state === "completed") void qc.invalidateQueries({ queryKey: KG_STATS_KEY });
    } catch (e) {
      message.error(errorText(e));
    }
  }

  const s = stats.data;
  const c = calibration.data;
  return (
    <ProCard bordered size="small" title={t("settings.kbm.kg_title", "知识图谱健康")}
      extra={<Button size="small" onClick={() => void stats.refetch()}>{t("settings.refresh", "刷新")}</Button>}>
      <Flex vertical gap={12}>
        {s && !s.enabled && <Alert type="info" message={t("settings.kbm.kg_off", "知识图谱未启用（FORMUNEX_KG_ENABLED）")} />}
        {s?.enabled && (
          <Space wrap>
            <Tag>{t("settings.kbm.entities", "实体 {{n}}", { n: s.entities })}</Tag>
            <Tag>{t("settings.kbm.mentions", "提及 {{n}}", { n: s.mentions })}</Tag>
            <Tag color={s.relation_layer_empty ? "warning" : "default"}>{t("settings.kbm.links", "关系链接 {{n}}", { n: s.links })}</Tag>
          </Space>
        )}
        {s?.relation_layer_empty && (
          <Alert type="warning" showIcon message={t("settings.kbm.rel_empty", "关系层为空：请开启「入库关系提取」或点「补语义关系」重建。")}
            description={(s.warnings ?? []).join(" ")} />
        )}
        <Space wrap>
          <Button loading={integrity.isPending} onClick={() => integrity.mutate()}>{t("settings.kbm.integrity", "KB 完整性扫描")}</Button>
          <Button loading={calibration.isPending} onClick={() => calibration.mutate()}>{t("settings.kbm.calibration", "KG 校准快照")}</Button>
          <Button loading={rebuild.isPending} onClick={() => modal.confirm({
            title: t("settings.kbm.rebuild_confirm", "重建知识图谱将从全部已入库资料重新提取实体/关系，耗时较长，确定执行？"),
            onOk: () => rebuild.mutate(),
          })}>{t("settings.kbm.rebuild", "重建知识图谱")}</Button>
          <Button loading={rel.running} onClick={() => modal.confirm({
            title: t("settings.kbm.relations_confirm", "补语义关系提取（实体已在库，只重跑关系）"),
            content: t("settings.kbm.relations_confirm_body", "默认最多处理 50 个源（成本帽），全库可能很慢。确定启动？"),
            onOk: () => void runRelations(),
          })}>{t("settings.kbm.relations", "补语义关系")}</Button>
          {rel.running && <Button danger onClick={() => void cancelRelations()}>{t("settings.cancel", "取消")}</Button>}
          {rel.taskId && !rel.running && <Button onClick={() => void refreshRelations()}>{t("settings.kbm.status", "查状态")}</Button>}
          {rel.status && <Typography.Text type="secondary">{rel.status}</Typography.Text>}
        </Space>
        {integrity.data && (
          <Alert type={integrity.data.healthy ? "success" : "warning"} showIcon message={integrity.data.healthy
            ? t("settings.kbm.healthy", "KB 无孤儿引用")
            : t("settings.kbm.orphans", "{{n}} 个孤儿引用", { n: integrity.data.total_orphans })
              + (integrity.data.external_backend ? t("settings.kbm.external", "（外部向量后端）") : "")} />
        )}
        {c && (
          <Descriptions size="small" bordered column={{ xs: 1, md: 3 }} items={[
            { key: "e", label: "KG", children: c.kg_enabled ? t("settings.enabled", "启用") : t("settings.disabled", "已禁用") },
            { key: "i", label: "inhibits ×", children: c.kg_inhibits_penalty },
            { key: "s", label: "synergizes +", children: c.kg_synergizes_bonus },
            { key: "m", label: "measured ×", children: c.kg_measured_bonus },
            ...(c.kg_measured_metric_bonus != null ? [{ key: "mm", label: "metric", children: `${c.kg_measured_metric_bonus} / ${c.kg_measured_metric_penalty ?? "—"}` }] : []),
            { key: "c", label: t("settings.kbm.hits", "关系命中"), children: `inhibits ${c.counts.inhibits} · substitutes ${c.counts.substitutes} · synergizes ${c.counts.synergizes}` },
          ]} />
        )}
      </Flex>
    </ProCard>
  );
}

/** KB keyword / hybrid probes, KG retrieve probe and the commercial product registry. */
function ProbeCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [kgStats, setKgStats] = useState<string | null>(null);
  const [pq, setPq] = useState("");
  const search = useMutation({
    mutationFn: async (mode: "search" | "hybrid" | "kg"): Promise<Hit[]> => {
      if (mode === "search") {
        const r = await call<{ results: Evidence[] }>("GET", `/api/kb/search?${new URLSearchParams({ q: q.trim(), k: "6" })}`);
        return fromEvidence(r.results ?? []);
      }
      if (mode === "hybrid") {
        const r = await call<KbChunk[]>("POST", "/api/kb/hybrid-search", { query: q.trim(), top_k: 8, alpha: 0.3 });
        return (r ?? []).map((c) => ({ key: c.id, title: c.heading_path || c.source_id || c.id, snippet: (c.text || "").slice(0, 220) }));
      }
      const r = await call<KgRetrieve>("POST", "/api/kg/retrieve", { query: q.trim(), mode: "auto", project_id: null, k_semantic: 6 });
      const s = r.stats ?? {};
      setKgStats(`mode=${r.plan?.mode ?? "?"} · semantic ${s.semantic_hits ?? 0} · mention ${s.mention_hits ?? 0} · chunks ${s.chunks_after_dedupe ?? "?"}`);
      return fromEvidence(r.evidence ?? []);
    },
    onMutate: (mode) => { if (mode !== "kg") setKgStats(null); },
    onSuccess: setHits,
    onError: (e) => { setHits(null); message.error(errorText(e)); },
  });
  const products = useMutation({
    mutationFn: () => call<{ products: KbProduct[]; total: number }>("GET", `/api/kb/products?${new URLSearchParams({ q: pq.trim(), limit: "30", offset: "0" })}`),
    onError: (e) => message.error(errorText(e)),
  });
  const can = !!q.trim() && !search.isPending;
  return (
    <ProCard bordered size="small" title={t("settings.kbm.probe", "检索探针")} split="horizontal">
      <ProCard size="small">
        <Space.Compact style={{ width: "100%" }}>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("settings.kbm.probe_ph", "检索词（如 盐雾 / epoxy）…")}
            onPressEnter={() => can && search.mutate("search")} />
          <Button disabled={!can} loading={search.isPending && search.variables === "search"} onClick={() => search.mutate("search")}>{t("settings.kbm.keyword", "关键词")}</Button>
          <Button disabled={!can} loading={search.isPending && search.variables === "hybrid"} onClick={() => search.mutate("hybrid")}>{t("settings.kbm.hybrid", "混合检索")}</Button>
          <Button disabled={!can} loading={search.isPending && search.variables === "kg"} onClick={() => search.mutate("kg")}>KG Retrieve</Button>
        </Space.Compact>
        {kgStats && <Typography.Text type="secondary">{kgStats}</Typography.Text>}
        <HitList hits={hits} />
      </ProCard>
      <ProCard size="small" title={t("settings.kbm.products", "商业产品牌号")}
        extra={products.data && t("settings.kbm.total", "共 {{n}} 条", { n: products.data.total })}>
        <Space.Compact style={{ width: "100%" }}>
          <Input value={pq} onChange={(e) => setPq(e.target.value)} placeholder={t("settings.kbm.products_ph", "牌号 / 供应商（如 Epon / Hexion）…")}
            onPressEnter={() => products.mutate()} />
          <Button loading={products.isPending} onClick={() => products.mutate()}>{t("settings.kbm.products_btn", "查询牌号")}</Button>
        </Space.Compact>
        {products.data && (
          <List size="small" dataSource={products.data.products} rowKey={(p) => `${p.trade_name}|${p.grade ?? ""}|${p.supplier ?? ""}`}
            locale={{ emptyText: t("settings.kbm.no_products", "无匹配牌号") }} style={{ maxHeight: 280, overflow: "auto" }}
            renderItem={(p) => (
              <List.Item extra={<Space>{p.supplier}{p.mention_count != null && <Tag>×{p.mention_count}</Tag>}</Space>}>
                <List.Item.Meta title={`${p.trade_name}${p.grade ? ` ${p.grade}` : ""}`}
                  description={[p.generic_name, p.cas, p.role].filter(Boolean).join(" · ") || "—"} />
              </List.Item>
            )} />
        )}
      </ProCard>
    </ProCard>
  );
}

/** Neo4j adapter layer (coexists with the SQLite graph): status, schema, browse, manual upserts and edges. */
function Neo4jCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [up, setUp] = useState({ name: "", cas: "", smiles: "" });
  const [link, setLink] = useState({ form: "", comp: "", ratio: 0.1 as number | null, formB: "", score: 1 as number | null });
  const [expanded, setExpanded] = useState<Record<string, Neo4jHit[]>>({});
  const [formComps, setFormComps] = useState<Record<string, Neo4jHit[]>>({});

  const stats = useQuery({ queryKey: [...NEO_KEY, "stats"], queryFn: () => call<Neo4jRawStats>("GET", "/api/kg/neo4j/stats"), enabled: open, retry: false });
  const sum = stats.data ? neo4jSummary(stats.data) : null;
  const reachable = sum?.reachable !== false && !stats.error;
  const compounds = useQuery({
    queryKey: [...NEO_KEY, "compounds", query],
    queryFn: () => call<Neo4jCompound[]>("GET", `/api/kg/neo4j/compounds?${new URLSearchParams(query ? { q: query, limit: "80" } : { limit: "80" })}`),
    enabled: open && reachable && !!stats.data,
  });
  const formulations = useQuery({
    queryKey: [...NEO_KEY, "formulations"],
    queryFn: () => call<Neo4jFormulation[]>("GET", "/api/kg/neo4j/formulations?limit=50"),
    enabled: open && reachable && !!stats.data,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: NEO_KEY });

  const schema = useMutation({
    mutationFn: () => call<{ ok: boolean }>("POST", "/api/kg/neo4j/schema/ensure", {}),
    onSuccess: (r) => {
      setOpen(true);
      if (r.ok) message.success(t("settings.neo.schema_ok", "Neo4j schema 已确保（约束/索引）"));
      else message.warning(t("settings.neo.schema_fail", "Neo4j schema ensure 未成功"));
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  type Upsert = { ok: boolean; message: string; uid?: string | null };
  const upsert = useMutation({
    mutationFn: (kind: "compound" | "formulation") => kind === "compound"
      ? call<Upsert>("POST", "/api/kg/neo4j/compounds", { name: up.name.trim(), cas_number: up.cas.trim() || undefined, smiles: up.smiles.trim() || undefined })
      : call<Upsert>("POST", "/api/kg/neo4j/formulations", { name: up.name.trim() }),
    // The endpoint answers 200 with ok=false when the Bolt write itself failed.
    onSuccess: (r, kind) => {
      if (!r.ok) return void message.error(r.message);
      message.success(t("settings.neo.upserted", "已写入：{{u}}", { u: r.uid ?? up.name }));
      if (kind === "compound") setQuery(up.name.trim());
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const linkEdge = useMutation({
    mutationFn: (kind: "contains" | "similar") => {
      const f = encodeURIComponent(link.form.trim());
      if (kind === "contains") {
        const qs = link.ratio != null ? `?ratio=${link.ratio}` : "";
        return call<{ ok: boolean; message: string }>("POST", `/api/kg/neo4j/formulations/${f}/compounds/${encodeURIComponent(link.comp.trim())}${qs}`, {});
      }
      return call<{ ok: boolean; message: string }>("POST",
        `/api/kg/neo4j/formulations/${f}/similar/${encodeURIComponent(link.formB.trim())}?score=${link.score ?? 1}`, {});
    },
    onSuccess: (r, kind) => {
      if (!r.ok) return void message.error(r.message);
      message.success(kind === "contains" ? t("settings.neo.contains_ok", "CONTAINS 边已写入") : t("settings.neo.similar_ok", "SIMILAR 边已写入"));
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  async function expandSimilar(uid: string) {
    if (expanded[uid]) return;
    try {
      const rows = await call<Neo4jHit[]>("GET", `/api/kg/neo4j/compounds/${encodeURIComponent(uid)}/similar`);
      setExpanded((m) => ({ ...m, [uid]: rows }));
    } catch (e) {
      message.error(errorText(e));
    }
  }
  async function expandFormulation(uid: string) {
    if (formComps[uid]) return;
    try {
      const rows = await call<Neo4jHit[]>("GET", `/api/kg/neo4j/formulations/${encodeURIComponent(uid)}/compounds`);
      setFormComps((m) => ({ ...m, [uid]: rows }));
    } catch (e) {
      message.error(errorText(e));
    }
  }

  return (
    <ProCard bordered size="small" title="Neo4j" subTitle={t("settings.neo.sub", "图谱适配层，与 SQLite 图谱共存")}
      extra={<Space>
        <Button size="small" loading={stats.isFetching} onClick={() => (open ? refresh() : setOpen(true))}>{t("settings.neo.status", "Neo4j 状态")}</Button>
        <Button size="small" loading={schema.isPending} onClick={() => schema.mutate()}>{t("settings.neo.ensure", "确保 Schema")}</Button>
      </Space>}>
      {!open ? (
        <Typography.Text type="secondary">{t("settings.neo.closed", "点「Neo4j 状态」连接并加载图谱数据。")}</Typography.Text>
      ) : (
        <Flex vertical gap={12}>
          <ErrorAlert error={stats.error} />
          {sum && (
            <Alert type={sum.reachable ? "success" : "warning"} showIcon message={sum.reachable
              ? t("settings.neo.ready", "就绪 · {{n}} 节点 / {{e}} 边 · {{c}} 化合物 · {{f}} 配方",
                { n: sum.nodes ?? "?", e: sum.edges ?? "?", c: sum.compounds ?? "?", f: sum.formulations ?? "?" })
              : t("settings.neo.unreachable", "Neo4j 当前不可达 — 仍可尝试写入（失败会显示错误）。")} />
          )}
          <ProCard size="small" bordered title={t("settings.neo.upsert", "手动写入图谱")}>
            <Space wrap>
              <Input style={{ width: 180 }} value={up.name} onChange={(e) => setUp({ ...up, name: e.target.value })} placeholder={t("settings.neo.name", "名称 *")} />
              <Input style={{ width: 140 }} value={up.cas} onChange={(e) => setUp({ ...up, cas: e.target.value })} placeholder="CAS" />
              <Input style={{ width: 220 }} value={up.smiles} onChange={(e) => setUp({ ...up, smiles: e.target.value })} placeholder="SMILES" />
              <Button disabled={!up.name.trim()} loading={upsert.isPending && upsert.variables === "compound"} onClick={() => upsert.mutate("compound")}>
                {t("settings.neo.write_compound", "写入化合物")}
              </Button>
              <Button disabled={!up.name.trim()} loading={upsert.isPending && upsert.variables === "formulation"} onClick={() => upsert.mutate("formulation")}>
                {t("settings.neo.write_formulation", "写入配方")}
              </Button>
            </Space>
          </ProCard>
          <ProCard size="small" bordered title={t("settings.neo.edges", "图谱边写入")}>
            <Flex vertical gap={8}>
              <Space wrap>
                <Input style={{ width: 200 }} value={link.form} onChange={(e) => setLink({ ...link, form: e.target.value })} placeholder={t("settings.neo.form_uid", "配方 uid *")} />
                <Input style={{ width: 200 }} value={link.comp} onChange={(e) => setLink({ ...link, comp: e.target.value })} placeholder={t("settings.neo.comp_uid", "化合物 uid")} />
                <InputNumber style={{ width: 110 }} value={link.ratio} onChange={(v) => setLink({ ...link, ratio: v })} placeholder="ratio" step={0.01} />
                <Button disabled={!link.form.trim() || !link.comp.trim()} loading={linkEdge.isPending && linkEdge.variables === "contains"}
                  onClick={() => linkEdge.mutate("contains")}>{t("settings.neo.contains", "CONTAINS 边")}</Button>
              </Space>
              <Space wrap>
                <Input style={{ width: 200 }} value={link.formB} onChange={(e) => setLink({ ...link, formB: e.target.value })} placeholder={t("settings.neo.form_b", "相似配方 uid")} />
                <InputNumber style={{ width: 110 }} value={link.score} onChange={(v) => setLink({ ...link, score: v })} placeholder="score" step={0.1} />
                <Button disabled={!link.form.trim() || !link.formB.trim()} loading={linkEdge.isPending && linkEdge.variables === "similar"}
                  onClick={() => linkEdge.mutate("similar")}>{t("settings.neo.similar", "SIMILAR 边")}</Button>
              </Space>
            </Flex>
          </ProCard>
          <Input.Search allowClear disabled={!reachable} placeholder={t("settings.neo.search_ph", "搜索化合物（名称/CAS/uid）…")}
            onSearch={(v) => setQuery(v.trim())} loading={compounds.isFetching} />
          <List size="small" rowKey="uid" loading={compounds.isLoading} dataSource={compounds.data ?? []} style={{ maxHeight: 360, overflow: "auto" }}
            locale={{ emptyText: t("settings.neo.no_compounds", "无匹配化合物") }}
            renderItem={(c) => (
              <List.Item actions={[<a key="s" onClick={() => void expandSimilar(c.uid)}>{t("settings.neo.co_occur", "共现化合物")}</a>]}>
                <List.Item.Meta
                  title={<Space wrap><span title={c.uid}>{c.name ?? c.uid}</span>{c.cas_number && <Tag>{c.cas_number}</Tag>}
                    {c.molecular_weight != null && <Typography.Text type="secondary">{c.molecular_weight} g/mol</Typography.Text>}</Space>}
                  description={<>
                    {c.smiles && <Typography.Text type="secondary" code>{c.smiles}</Typography.Text>}
                    {expanded[c.uid] && (
                      <div>{expanded[c.uid].length === 0 ? "—" : expanded[c.uid].map((s, i) => (
                        <Tag key={s.uid ?? i}>{s.name ?? s.uid}{s.co_count != null ? ` ×${s.co_count}` : ""}</Tag>
                      ))}</div>
                    )}
                  </>}
                />
              </List.Item>
            )} />
          {(formulations.data?.length ?? 0) > 0 && (
            <ProCard size="small" bordered title={t("settings.neo.formulations", "配方（图库）")}>
              <List size="small" rowKey="uid" dataSource={formulations.data ?? []} style={{ maxHeight: 240, overflow: "auto" }}
                renderItem={(f) => (
                  <List.Item extra={formComps[f.uid] && t("settings.neo.components", "{{n}} 组分", { n: formComps[f.uid].length })}>
                    <a title={f.uid} onClick={() => void expandFormulation(f.uid)}>{f.name ?? f.uid}{f.target_property ? ` · ${f.target_property}` : ""}</a>
                  </List.Item>
                )} />
            </ProCard>
          )}
        </Flex>
      )}
    </ProCard>
  );
}

/** KB / KG maintenance and diagnostics (moved out of the legacy dependency manager). */
export function KbMaintenanceTab() {
  return (
    <Flex vertical gap={16}>
      <KgHealthCard />
      <ProbeCard />
      <Neo4jCard />
    </Flex>
  );
}
