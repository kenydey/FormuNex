import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Descriptions, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography, theme } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import { fmtScore, loadTrend, probeScopeParams, pushTrend, rankDelta, trendDropped, type ProbeScope, type TrendPoint } from "./logic";
import type { GoldenResponse, ProbeHit, ProbeMode, ProbeResponse } from "./types";

const SAMPLES = ["硅烷偶联剂", "磷化液", "钝化膜 铬酸盐", "水性环氧 盐雾"];

/** Scored multi-recall probe of KB chunk retrieval (POST /api/kb/query-test) + golden batch with an MRR trend. */
export function RetrievalProbe() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const projectId = useUi((s) => s.projectId);
  const [query, setQuery] = useState(SAMPLES[0]);
  const [mode, setMode] = useState<ProbeMode>("hybrid");
  const [scope, setScope] = useState<ProbeScope>("project_global");
  const [topK, setTopK] = useState(10);
  const [alpha, setAlpha] = useState(0.3);
  const alphaTouched = useRef(false);
  const [selected, setSelected] = useState<ProbeHit | null>(null);
  const [json, setJson] = useState(false);
  const [trend, setTrend] = useState<TrendPoint[]>(() => loadTrend());
  const settings = useQuery({ queryKey: ["kb", "retrieval-settings"], queryFn: () => call<{ kb_hybrid_alpha: number }>("GET", "/api/kb/retrieval-settings") });
  const serverAlpha = Number.isFinite(Number(settings.data?.kb_hybrid_alpha)) ? Number(settings.data?.kb_hybrid_alpha) : null;
  useEffect(() => { if (serverAlpha != null && !alphaTouched.current) setAlpha(Math.max(0, Math.min(1, serverAlpha))); }, [serverAlpha]);
  const sp = probeScopeParams(projectId, scope);
  const body = { mode, top_k: topK, alpha, ...sp, rerank: mode === "hybrid_rerank" ? true : null };

  const probe = useMutation({
    mutationFn: () => call<ProbeResponse>("POST", "/api/kb/query-test", { query: query.trim(), ...body }),
    onSuccess: (r) => setSelected(r.hits?.[0] ?? null),
  });
  const golden = useMutation({
    mutationFn: () => call<GoldenResponse>("POST", "/api/kb/golden-eval/run", { ...body, top_k: Math.min(topK, 5) }),
    onSuccess: (g) => {
      if (typeof g.mrr === "number" || typeof g.recall_at_k === "number") {
        setTrend(pushTrend({ at: new Date().toISOString(), mrr: Number(g.mrr ?? 0),
          recall_at_k: Number(g.recall_at_k ?? (g.total ? g.passed / g.total : 0)), passed: g.passed, total: g.total }));
      }
    },
  });
  const res = probe.data;
  const drops = res?.gate_drops?.retrieval;
  const dropN = drops ? (drops.blocked_domain || 0) + (drops.garbage_snippet || 0) + (drops.wiki_track || 0) : 0;
  const trendOption = useMemo(() => ({
    tooltip: { trigger: "axis" as const }, legend: { data: ["MRR", "Recall@k"] }, grid: { left: 40, right: 16, top: 32, bottom: 24 },
    xAxis: { type: "category" as const, data: trend.map((p) => new Date(p.at).toLocaleDateString()) },
    yAxis: { type: "value" as const, min: 0, max: 1 },
    series: [
      { name: "MRR", type: "line" as const, data: trend.map((p) => Number(p.mrr.toFixed(3))), color: token.colorPrimary },
      { name: "Recall@k", type: "line" as const, data: trend.map((p) => Number(p.recall_at_k.toFixed(3))), color: token.colorSuccess },
    ],
  }), [trend, token.colorPrimary, token.colorSuccess]);

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert type="info" showIcon message={<span>
        {projectId ? t("library.probe.project", "当前项目 {{p}}", { p: projectId.slice(0, 8) }) : t("library.probe.no_project", "未选项目 → 按全局语料检索")}
        {" · "}{t("library.probe.hint", "Wiki 全文检索 / 结构化学检索不在本页；α=0≈向量、α=1≈BM25")}
        {serverAlpha != null && <> · {t("library.probe.server_alpha", "与推荐共享默认 α={{a}}", { a: serverAlpha })}</>}
      </span>} />
      <Space.Compact style={{ width: "100%" }}>
        <Input value={query} onChange={(e) => setQuery(e.target.value)} onPressEnter={() => query.trim() && probe.mutate()}
          placeholder={t("library.probe.placeholder", "输入化学专业检索词…")} />
        <Button type="primary" loading={probe.isPending} disabled={!query.trim()} onClick={() => probe.mutate()}>{t("library.probe.run", "运行")}</Button>
        <Button loading={golden.isPending} onClick={() => golden.mutate()}>{t("library.probe.golden", "Golden 批跑")}</Button>
      </Space.Compact>
      <Space wrap>{SAMPLES.map((s) => <Tag.CheckableTag key={s} checked={query === s} onChange={() => setQuery(s)}>{s}</Tag.CheckableTag>)}</Space>
      <Form layout="inline" style={{ rowGap: 8 }}>
        <Form.Item label={t("library.probe.mode", "模式")}>
          <Select value={mode} onChange={setMode} style={{ width: 140 }} options={[
            { value: "keyword", label: t("library.probe.keyword", "关键词") }, { value: "hybrid", label: t("library.probe.hybrid", "混合") },
            { value: "hybrid_rerank", label: t("library.probe.rerank", "混合 + 重排") }]} />
        </Form.Item>
        <Form.Item label={t("library.probe.scope", "作用域")}>
          <Select value={scope} onChange={setScope} style={{ width: 150 }} options={[
            { value: "project_global", label: t("library.probe.s_pg", "项目 + 全局") }, { value: "project", label: t("library.probe.s_p", "仅当前项目") },
            { value: "global", label: t("library.probe.s_g", "全局") }]} />
        </Form.Item>
        <Form.Item label="top_k"><InputNumber min={1} max={50} value={topK} onChange={(v) => setTopK(Math.max(1, Math.min(50, Number(v) || 10)))} /></Form.Item>
        <Form.Item label="alpha (BM25)">
          <InputNumber min={0} max={1} step={0.1} value={alpha} onChange={(v) => { alphaTouched.current = true; setAlpha(Math.max(0, Math.min(1, Number(v) || 0))); }} />
        </Form.Item>
      </Form>
      <ErrorAlert error={probe.error ?? golden.error} />
      {res?.warning && <Alert type="warning" showIcon message={res.warning} />}
      {res && (
        <ProCard bordered title={<Space wrap size={12}>
          <span>vector_mode=<Typography.Text code>{res.vector_mode}</Typography.Text></span>
          <span>elapsed=<Typography.Text code>{res.elapsed_ms}ms</Typography.Text></span>
          <span>rerank=<Typography.Text code>{res.params.rerank_applied ? "applied" : "off"}</Typography.Text></span>
          {drops && <Typography.Text type={dropN ? "warning" : "secondary"}>{t("library.probe.drops", "本轮门禁丢弃={{n}}", { n: dropN })}
            {drops.blocked_domain ? ` · ${t("library.probe.d_domain", "域名")} ${drops.blocked_domain}` : ""}
            {drops.garbage_snippet ? ` · ${t("library.probe.d_garbage", "垃圾片段")} ${drops.garbage_snippet}` : ""}
            {drops.wiki_track ? ` · wiki ${drops.wiki_track}` : ""}</Typography.Text>}
        </Space>} extra={<Space><Switch size="small" checked={json} onChange={setJson} />{t("library.probe.json", "原始 JSON")}</Space>}>
          {json ? <pre style={{ maxHeight: 420, overflow: "auto", fontSize: 12 }}>{JSON.stringify(res, null, 2)}</pre> : (
            <>
              <Table<ProbeHit> size="small" pagination={false} scroll={{ y: 360 }} dataSource={res.hits ?? []}
                rowKey={(h) => `${h.rank}-${h.chunk_id || h.source_id || h.title}`}
                onRow={(h) => ({ onClick: () => setSelected(h), style: { cursor: "pointer", background: selected?.rank === h.rank ? token.controlItemBgActive : undefined } })}
                locale={{ emptyText: t("library.probe.no_hits", "无命中") }}
                columns={[
                  { title: "#", dataIndex: "rank", width: 48 },
                  { title: t("library.probe.title", "来源 / 标题"), render: (_v, h) => h.title || h.source_id || "—", ellipsis: true },
                  { title: "bm25", width: 80, align: "right", render: (_v, h) => fmtScore(h.bm25_score) },
                  { title: "cosine", width: 80, align: "right", render: (_v, h) => fmtScore(h.cosine_score) },
                  { title: "hybrid", width: 80, align: "right", render: (_v, h) => fmtScore(h.hybrid_score ?? h.relevance) },
                  { title: "rerank", width: 80, align: "right", render: (_v, h) => fmtScore(h.rerank_score) },
                  { title: "Δ", width: 56, align: "right", render: (_v, h) => rankDelta(h) },
                ]} />
              {selected && (
                <Descriptions size="small" column={1} style={{ marginTop: 12 }} bordered>
                  <Descriptions.Item label={selected.title || "—"}>
                    <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{selected.snippet || t("library.probe.no_snippet", "（无片段）")}</Typography.Paragraph>
                  </Descriptions.Item>
                </Descriptions>
              )}
            </>
          )}
        </ProCard>
      )}
      {golden.data && (
        <ProCard bordered title={<Space wrap>
          {t("library.probe.golden_head", "Golden 批跑 · {{p}}/{{n}} 通过", { p: golden.data.passed, n: golden.data.total })}
          <Tag>mode={golden.data.mode}</Tag>
          {typeof golden.data.mrr === "number" && <Tag color="processing">MRR {golden.data.mrr.toFixed(3)}</Tag>}
          {typeof golden.data.recall_at_k === "number" && <Tag>Recall@{golden.data.top_k} {golden.data.recall_at_k.toFixed(3)}</Tag>}
        </Space>}>
          {trendDropped(trend) && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={t("library.probe.dropped", "MRR 较上次下降")} />}
          {trend.length >= 2 && <Chart option={trendOption} height={200} />}
          <Table size="small" pagination={false} rowKey="question" dataSource={golden.data.results} scroll={{ y: 280 }}
            columns={[
              { title: t("library.probe.result", "结果"), width: 80, render: (_v, r) => <Tag color={r.passed ? "success" : "error"}>{r.passed ? "PASS" : "FAIL"}</Tag> },
              { title: t("library.probe.question", "问题"), dataIndex: "question" },
              { title: t("library.probe.matched", "命中关键词"), dataIndex: "matched_keyword", width: 160 },
            ]} />
        </ProCard>
      )}
    </Space>
  );
}

export default function RetrievalProbePage() {
  const { t } = useTranslation();
  return <PageContainer subTitle={t("library.probe.sub", "多路召回分数 · Golden 批跑 · MRR 趋势")}><RetrievalProbe /></PageContainer>;
}
