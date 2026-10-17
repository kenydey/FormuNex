import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { Alert, App, Button, Drawer, Empty, Form, Input, InputNumber, List, Popconfirm, Segmented, Select, Space, Table, Tag, Timeline, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask } from "@/api/stream";
import { Markdown } from "@/shared/Markdown";
import { ErrorAlert, errorText } from "@/shared/feedback";
import {
  SCREENING_COLOR, buildManifestMarkdown, fmtEpoch, formatLocator, hasFulltext, locatorBody, manifestCoverage, scopedManifestItems,
  shelfFilename, splitList, type ManifestStyle,
} from "./logic";
import { WithProject } from "./shared";
import type { Manifest, ManifestItem, RuleHistory, RuleVersion, ScreeningEval } from "./types";

const manifestKey = (projectId: string) => ["literature", "manifest", projectId];

/** Capture / freeze / unfreeze / OA enrich, and keyword / preset screening with versioned rules and evaluation. */
function FreezeStrip({ projectId, manifest }: { projectId: string; manifest: Manifest | undefined }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [form] = Form.useForm<{ preset?: string; include: string; exclude: string; rule_name?: string }>();
  const [evalRes, setEvalRes] = useState<ScreeningEval | null>(null);
  const [rollback, setRollback] = useState<string | undefined>();
  const presets = useQuery({ queryKey: ["literature", "presets"],
    queryFn: () => call<{ presets: { name: string; title: string }[] }>("GET", "/api/wiki/literature/screening/presets") });
  const rules = useQuery({ queryKey: ["literature", "rules", projectId],
    queryFn: () => call<{ versions: RuleVersion[]; history: RuleHistory[]; current_rule_name?: string | null }>("GET",
      `/api/wiki/literature/screening/rule-versions?project_id=${encodeURIComponent(projectId)}`) });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: manifestKey(projectId) });
    void qc.invalidateQueries({ queryKey: ["literature", "rules", projectId] });
    void qc.invalidateQueries({ queryKey: ["literature", "library", projectId] });
  };
  const run = useMutation({
    mutationFn: async ({ fn }: { label: string; fn: () => Promise<unknown> }) => fn(),
    onSuccess: (r, v) => { message.success(typeof r === "string" ? r : v.label); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const act = (label: string, fn: () => Promise<unknown>) => run.mutate({ label, fn });
  const criteria = () => {
    const v = form.getFieldsValue();
    return { include_keywords: splitList(v.include ?? ""), exclude_keywords: splitList(v.exclude ?? "") };
  };
  const frozen = manifest?.frozen;
  const cand = manifest?.coverage?.candidate_count ?? 0;

  return (
    <ProCard bordered style={{ marginBottom: 12 }} title={t("library.manifest.strip", "文献冻结")}
      subTitle={<Typography.Text type="secondary">{t("library.manifest.stats", "候选 {{c}} · 已冻结 {{f}} · digest {{d}}",
        { c: cand, f: manifest?.coverage?.frozen_count ?? 0, d: frozen?.digest?.slice(0, 10) ?? "—" })}</Typography.Text>}
      extra={<Space wrap>
        <Button loading={run.isPending} onClick={() => act(t("library.manifest.captured", "已捕获"), () => call("POST", "/api/wiki/literature/capture", { project_id: projectId }))}>
          {t("library.manifest.capture", "捕获 Capture")}</Button>
        <Popconfirm title={t("library.manifest.freeze_q", "冻结当前候选集为评审语料？")} onConfirm={() => act(t("library.manifest.frozen_ok", "已冻结"),
          () => call("POST", "/api/wiki/literature/freeze", { project_id: projectId, actor: "hub" }))}>
          <Button type="primary" loading={run.isPending}>{t("library.manifest.freeze", "冻结 Freeze")}</Button>
        </Popconfirm>
        <Button disabled={!frozen} loading={run.isPending} onClick={() => act(t("library.manifest.unfrozen", "已解冻"),
          () => call("POST", "/api/wiki/literature/unfreeze", { project_id: projectId, actor: "hub" }))}>{t("library.manifest.unfreeze", "解冻")}</Button>
        <Button disabled={cand < 1} loading={run.isPending} onClick={() => act("", async () => {
          const r = await call<{ attempted?: number; fetched?: number; skipped?: number }>("POST", "/api/wiki/literature/enrich-oa",
            { project_id: projectId, scope: "missing_fulltext", limit: 20, actor: "hub" });
          return t("library.manifest.enrich_result", "补全文 {{f}}/{{a}} · 跳过 {{s}}", { f: r.fetched ?? 0, a: r.attempted ?? 0, s: r.skipped ?? 0 });
        })}>{t("library.manifest.enrich", "补全文")}</Button>
      </Space>}
      collapsible defaultCollapsed={false}>
      <Form form={form} layout="inline" initialValues={{ include: "epoxy,coating", exclude: "" }} style={{ rowGap: 8 }}>
        <Form.Item name="preset" label={t("library.screen.preset", "预设")}>
          <Select allowClear style={{ width: 200 }} placeholder={t("library.screen.no_preset", "无（使用关键词）")}
            options={(presets.data?.presets ?? []).map((p) => ({ value: p.name, label: p.title }))} />
        </Form.Item>
        <Form.Item name="include" label={t("library.screen.include", "纳入")}><Input style={{ width: 200 }} /></Form.Item>
        <Form.Item name="exclude" label={t("library.screen.exclude", "排除")}><Input style={{ width: 160 }} /></Form.Item>
        <Form.Item name="rule_name" label={t("library.screen.rule_name", "规则版本名")}><Input style={{ width: 140 }} /></Form.Item>
      </Form>
      <Space wrap style={{ marginTop: 12 }}>
        <Button type="primary" loading={run.isPending} onClick={() => act(t("library.screen.done", "筛选完成"), async () => {
          const v = form.getFieldsValue();
          const res = await call<{ async?: boolean; task_id?: string }>("POST", "/api/wiki/literature/screen", {
            project_id: projectId, apply: true, preset: v.preset || undefined, rule_name: v.rule_name?.trim() || undefined, criteria: criteria(),
          });
          if (res.async && res.task_id) await awaitTask(res.task_id);
        })}>{t("library.screen.run", "运行筛选")}</Button>
        <Button loading={run.isPending} onClick={() => {
          const name = form.getFieldValue("rule_name")?.trim();
          if (!name) { message.warning(t("library.screen.need_name", "请先填写规则版本名")); return; }
          act(t("library.screen.saved", "规则版本已保存"), () => call("POST", "/api/wiki/literature/screening/rule-versions",
            { project_id: projectId, name, criteria: criteria(), created_by: "hub" }));
        }}>{t("library.screen.save", "保存版本")}</Button>
        <Button loading={run.isPending} onClick={() => act(t("library.screen.evaluated", "评估完成"), async () => {
          setEvalRes(await call<ScreeningEval>("POST", "/api/wiki/literature/screening/evaluate", { project_id: projectId, criteria: criteria() }));
        })}>{t("library.screen.evaluate", "效果评估")}</Button>
        {(rules.data?.versions?.length ?? 0) > 0 && (
          <Space.Compact>
            <Select style={{ width: 260 }} value={rollback} onChange={setRollback}
              placeholder={t("library.screen.pick_version", "选择历史版本回滚…（当前 {{n}}）", { n: rules.data?.current_rule_name ?? "—" })}
              options={rules.data!.versions.map((v) => ({ value: v.version, label: `${v.name} · ${v.version.slice(0, 8)}${v.changelog ? ` · ${v.changelog.slice(0, 20)}` : ""}` }))} />
            <Button disabled={!rollback} onClick={() => act(t("library.screen.rolled_back", "已回滚"), async () => {
              await call("POST", "/api/wiki/literature/screening/rule-versions/rollback", { project_id: projectId, version: rollback, actor: "hub" });
              setRollback(undefined);
            })}>{t("library.screen.rollback", "回滚")}</Button>
          </Space.Compact>
        )}
      </Space>
      {evalRes && (
        <Alert style={{ marginTop: 12 }} type={evalRes.evaluated ? "info" : "warning"} showIcon closable onClose={() => setEvalRes(null)}
          message={evalRes.evaluated
            ? t("library.screen.eval_ok", "人工标注 {{n}} 条 · P={{p}} R={{r}} F1={{f}} · TP {{tp}} / FP {{fp}} / TN {{tn}} / FN {{fn}}", {
              n: evalRes.labeled_count, p: evalRes.metrics?.precision.toFixed(2), r: evalRes.metrics?.recall.toFixed(2), f: evalRes.metrics?.f1.toFixed(2),
              tp: evalRes.confusion?.tp, fp: evalRes.confusion?.fp, tn: evalRes.confusion?.tn, fn: evalRes.confusion?.fn })
            : t("library.screen.eval_few", "样本不足（人工标注 {{n}} 条，需 ≥5 条），无法评估", { n: evalRes.labeled_count ?? 0 })}
          description={evalRes.evaluated && (evalRes.include_ablation ?? []).length > 0
            ? t("library.screen.ablation", "最伤召回的关键词：{{k}}", { k: (evalRes.include_ablation ?? []).slice(0, 3).map((a) => `${a.keyword} (${a.recall_delta.toFixed(2)})`).join("  ") })
            : undefined} />
      )}
      {(rules.data?.history?.length ?? 0) > 0 && (
        <Timeline style={{ marginTop: 16, marginBottom: 0 }} items={rules.data!.history.slice(-8).reverse().map((h, i) => ({
          key: `${h.at}-${i}`,
          children: <Typography.Text type="secondary">{fmtEpoch(h.at)} {h.actor} {h.action === "rolled_back" ? t("library.screen.h_rollback", "回滚到") : t("library.screen.h_save", "保存")} {h.name}
            {h.changelog ? `：${h.changelog.slice(0, 40)}` : ""}</Typography.Text>,
        }))} />
      )}
    </ProCard>
  );
}

/** Read-only citation list of the (frozen) manifest, coverage cells, locator annotations and style snapshots to the project shelf. */
function ManifestDetail({ projectId, manifest }: { projectId: string; manifest: Manifest }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [style, setStyle] = useState<ManifestStyle>("list");
  const [locEdit, setLocEdit] = useState<ManifestItem | null>(null);
  const [locForm] = Form.useForm<{ page?: number | null; figure?: string; table?: string }>();
  const frozen = manifest.frozen;
  const items = useMemo(() => scopedManifestItems(manifest.items, frozen), [manifest.items, frozen]);
  const cov = manifestCoverage(items);
  const digest = frozen?.digest?.slice(0, 10) ?? "—";

  const saveLoc = useMutation({
    mutationFn: async (v: { page?: number | null; figure?: string; table?: string }) => {
      const body = locatorBody(v);
      if (!body) throw new Error(t("library.manifest.bad_page", "页码必须是 ≥1 的整数"));
      return call("PUT", `/api/wiki/literature/items/${encodeURIComponent(locEdit!.id)}/locator`, { project_id: projectId, ...body });
    },
    onSuccess: () => { setLocEdit(null); message.success(t("common.saved", "已保存")); void qc.invalidateQueries({ queryKey: manifestKey(projectId) }); },
    onError: (e) => message.error(errorText(e)),
  });
  const snapshot = useMutation({
    mutationFn: async () => {
      const md = buildManifestMarkdown(style, `文献 Manifest 快照（digest ${digest}）`, items);
      const filename = shelfFilename(`manifest_${digest}`, "md");
      await call("POST", `/api/projects/${encodeURIComponent(projectId)}/exports`, { filename, content: md });
      return filename;
    },
    onSuccess: (f) => message.success(t("library.manifest.snapshot_saved", "已存新版：{{f}}", { f })),
    onError: (e) => message.error(errorText(e)),
  });
  const preview = useMemo(() => buildManifestMarkdown(style, t("library.manifest.preview_head", "预览"), items), [style, items, t]);

  const cells: [string, number][] = [
    [frozen ? t("library.manifest.c_frozen", "冻结条目") : t("library.manifest.c_cand", "候选条目"), cov.total],
    [t("library.manifest.c_doi", "有 DOI"), cov.doi], [t("library.manifest.c_snippet", "有摘要"), cov.snippet], [t("library.manifest.c_full", "有全文"), cov.fulltext],
    [t("library.manifest.c_match", "筛选命中"), cov.match], [t("library.manifest.c_nomatch", "筛选排除"), cov.noMatch], [t("library.manifest.c_pending", "待筛选"), cov.pending],
  ];

  return (
    <>
      <ProCard bordered style={{ marginBottom: 12 }} title={t("library.manifest.detail", "Manifest 详情")}
        subTitle={frozen ? <Tag color="cyan">{t("library.manifest.frozen_meta", "已冻结 · {{at}} · {{a}} · digest {{d}}", { at: fmtEpoch(frozen.at), a: frozen.actor, d: digest })}</Tag>
          : <Tag color="warning">{t("library.manifest.not_frozen", "未冻结：当前为候选集")}</Tag>}>
        <StatisticCard.Group direction="row">
          {cells.map(([title, value]) => <StatisticCard key={title} statistic={{ title, value }} />)}
        </StatisticCard.Group>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          {t("library.manifest.honesty", "诚实声明：以上计数 = 冻结时返回给 Agent 的文献集合（frozen review corpus），不是人工已逐篇读完。")}
        </Typography.Paragraph>
      </ProCard>
      <ProCard split="vertical" bordered>
        <ProCard colSpan="55%" title={t("library.manifest.list", "引用列表（{{n}}）· 只读", { n: items.length })}>
          <Table<ManifestItem> size="small" rowKey="id" dataSource={items} pagination={{ pageSize: 15 }}
            locale={{ emptyText: <Empty description={t("library.manifest.empty", "暂无条目：先 Capture / Freeze")} /> }}
            columns={[
              { title: t("library.lib.f_title", "标题"), render: (_v, it) => (
                <Space direction="vertical" size={0}>
                  <Link to={`/knowledge/sources/${encodeURIComponent(it.id)}`}>{(it.title || it.id).slice(0, 90)}</Link>
                  {it.doi && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{it.doi}</Typography.Text>}
                </Space>) },
              { title: t("library.manifest.locator", "定位"), width: 130, render: (_v, it) => (
                <Button size="small" type="link" onClick={() => { setLocEdit(it); locForm.setFieldsValue({ page: it.locator?.page ?? null, figure: it.locator?.figure ?? "", table: it.locator?.table ?? "" }); }}>
                  {formatLocator(it) || t("library.manifest.annotate", "标注")}
                </Button>) },
              { title: t("library.lib.screening", "筛选态"), width: 100, render: (_v, it) => <Tag color={SCREENING_COLOR[it.screening || "unset"]}>{it.screening || "unset"}</Tag> },
              { title: t("library.manifest.fulltext", "全文"), width: 60, render: (_v, it) => (hasFulltext(it) ? <Tag color="blue">✓</Tag> : "—") },
            ]} />
        </ProCard>
        <ProCard title={t("library.manifest.style", "样式预览")}
          extra={<Space>
            <Segmented<ManifestStyle> value={style} onChange={setStyle} options={[
              { value: "list", label: t("library.manifest.s_list", "列表") }, { value: "citation", label: t("library.manifest.s_citation", "引用") },
              { value: "table", label: t("library.manifest.s_table", "表格") }]} />
            <Button type="primary" disabled={!items.length} loading={snapshot.isPending} onClick={() => snapshot.mutate()}>{t("library.manifest.save_snapshot", "存新版")}</Button>
          </Space>}>
          <Typography.Paragraph type="secondary">{t("library.manifest.preview_hint", "预览只读，不修改 Manifest；「存新版」把快照保存到项目文件架。")}</Typography.Paragraph>
          <div style={{ maxHeight: 520, overflow: "auto" }}><Markdown content={preview} /></div>
        </ProCard>
      </ProCard>
      <Drawer open={!!locEdit} onClose={() => setLocEdit(null)} destroyOnHidden width={420}
        title={t("library.manifest.locator_title", "引用定位器 · {{t}}", { t: (locEdit?.title || locEdit?.id || "").slice(0, 40) })}
        extra={<Button type="primary" loading={saveLoc.isPending} onClick={() => locForm.submit()}>{t("common.save", "保存")}</Button>}>
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={t("library.manifest.locator_hint", "仅标注元数据，不改变冻结语料；三项全空 = 清除定位")} />
        <Form form={locForm} layout="vertical" onFinish={(v) => saveLoc.mutate(v)}>
          <Form.Item name="page" label={t("library.manifest.page", "页")}><InputNumber min={1} precision={0} style={{ width: "100%" }} /></Form.Item>
          <Form.Item name="figure" label={t("library.manifest.figure", "图")}><Input maxLength={60} /></Form.Item>
          <Form.Item name="table" label={t("library.manifest.table", "表")}><Input maxLength={60} /></Form.Item>
        </Form>
      </Drawer>
    </>
  );
}

function ManifestBody({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: manifestKey(projectId), queryFn: () => call<Manifest>("GET", `/api/wiki/literature/${encodeURIComponent(projectId)}`) });
  return (
    <>
      <ErrorAlert error={q.error} />
      <FreezeStrip projectId={projectId} manifest={q.data} />
      {q.data && <ManifestDetail projectId={projectId} manifest={q.data} />}
      {q.isLoading && <List loading />}
    </>
  );
}

/** Literature manifest of the current project: freeze strip + screening + frozen-corpus detail. */
export default function ManifestPage() {
  const { t } = useTranslation();
  return (
    <PageContainer subTitle={t("library.manifest.sub", "捕获 → 筛选 → 冻结评审语料；引用列表只读、可标注定位器")}>
      <WithProject>{(projectId) => <ManifestBody key={projectId} projectId={projectId} />}</WithProject>
    </PageContainer>
  );
}

