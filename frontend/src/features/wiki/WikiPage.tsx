import { DownOutlined, NodeIndexOutlined, ReloadOutlined } from "@ant-design/icons";
import { PageContainer } from "@ant-design/pro-components";
import {
  Alert, App, Button, Card, Checkbox, Col, Descriptions, Dropdown, Empty, Input, List, Row, Select, Skeleton, Space, Tag, Tooltip,
  Typography, theme, type MenuProps,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { envFlag, wikiApi } from "./api";
import { WIKI_KINDS } from "./graph";
import { dossierProjectId, themeKey, wikiHref, wikiPathFromHref } from "./paths";
import { useDebounced, wikiRows } from "./rows";
import type { WikiFlagAction, WikiLintResult, WikiListRow, WikiSweepResult } from "./types";
import { WikiReader } from "./WikiReader";

type Op = { label: string; fn: () => Promise<string | void> };

/** Wiki 知识库 (legacy HubWikiPane): project-scoped page browser + reader, FTS search, lint flags, dossier, maintenance. */
export default function WikiPage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { token } = theme.useToken();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const projectId = useUi((s) => s.projectId);
  const selectedPath = wikiPathFromHref(location.pathname);

  const [kind, setKind] = useState<string | undefined>();
  const [flagsOnly, setFlagsOnly] = useState(false);
  const [reviewedOnly, setReviewedOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [summary, setSummary] = useState<string | null>(null);
  const q = useDebounced(query.trim(), 200);
  const searching = !flagsOnly && q.length >= 2;

  const pages = useQuery({ queryKey: ["wiki", "pages", projectId, kind ?? ""], queryFn: () => wikiApi.pages(projectId!, kind), enabled: !!projectId });
  const flags = useQuery({ queryKey: ["wiki", "flags", projectId], queryFn: () => wikiApi.flags(projectId!), enabled: !!projectId && flagsOnly });
  const search = useQuery({ queryKey: ["wiki", "search", projectId, kind ?? "", q], queryFn: () => wikiApi.search(q, projectId, kind), enabled: !!projectId && searching });
  const env = useQuery({ queryKey: ["settings", "env-flags"], queryFn: wikiApi.envFlags, retry: false });
  const embedEnabled = envFlag(env.data?.flags, "wiki_embed_enabled");
  const detail = useQuery({ queryKey: ["wiki", "page", selectedPath], queryFn: () => wikiApi.byPath(selectedPath!), enabled: !!selectedPath });
  const dossierPid = dossierProjectId(selectedPath);
  const dossier = useQuery({ queryKey: ["wiki", "dossier", dossierPid], queryFn: () => wikiApi.dossier(dossierPid!), enabled: !!dossierPid, retry: false });

  const rows = useMemo(() => wikiRows({
    pages: pages.data?.pages ?? [], flagPages: flags.data?.pages, hits: searching ? search.data?.hits ?? null : null, flagsOnly, reviewedOnly, query,
  }), [pages.data, flags.data, search.data, searching, flagsOnly, reviewedOnly, query]);
  const linkPages = useMemo(() => (pages.data?.pages ?? []).map((p) => ({ path: p.path, title: p.title || p.path, norm_key: p.norm_key, kind: p.kind })), [pages.data]);

  const op = useMutation({
    mutationFn: (o: Op) => o.fn(),
    onSuccess: (msg) => {
      if (msg) setSummary(msg);
      void qc.invalidateQueries({ queryKey: ["wiki"] });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const busy = op.isPending ? op.variables?.label : null;
  const run = (label: string, fn: Op["fn"]) => op.mutate({ label, fn });
  const open = (path: string) => navigate(wikiHref(path));

  const lint = () => run("lint", async () => {
    const o = await call<WikiLintResult>("POST", "/api/wiki/lint/run", { limit: 200, detect_orphan: true });
    setFlagsOnly(true);
    return t("wiki.lint_summary", "Lint：扫描 {{s}} · 有旗标 {{f}} · 孤儿 {{o}} · 断链页 {{b}}", { s: o.scanned ?? "?", f: o.flagged ?? "?", o: o.orphan_count ?? "?", b: o.broken_count ?? "?" });
  });
  const sweep = () => run("sweep", async () => {
    const o = await call<WikiSweepResult>("POST", "/api/wiki/lint/sweep", { limit: 200, detect_orphan: true });
    setFlagsOnly(true);
    return t("wiki.sweep_summary", "清理：重扫 {{s}} · 清除 {{c}} · 仍有旗标 {{f}}", { s: o.scanned ?? "?", c: o.cleared ?? "?", f: o.still_flagged ?? "?" });
  });
  const openDossier = () => run("dossier-open", async () => {
    if (!projectId) return;
    let page;
    try {
      page = await wikiApi.dossier(projectId);
    } catch {
      // not generated yet (404) → create it from the live pack, then open it
      await call("POST", "/api/wiki/dossier/ensure", { project_id: projectId });
      page = await wikiApi.dossier(projectId);
    }
    open(page.path);
  });
  const refreshDossier = () => run("dossier-refresh", async () => {
    if (!projectId) return;
    const o = await call<{ ok: boolean; path?: string; patched_sections?: string[] }>("POST", "/api/wiki/dossier/refresh", { project_id: projectId });
    if (o.path) open(o.path);
    return t("wiki.dossier_refreshed", "卷宗已刷新：{{path}}", { path: o.path ?? "—" });
  });
  const compileTheme = (key: string) => run("theme", async () => {
    const o = await call<{ ok: boolean; path?: string; error?: string }>("POST", "/api/wiki/themes/compile", { system_key: key, use_llm: false });
    if (!o.ok) throw new Error(o.error || "compile failed");
    return t("wiki.theme_compiled", "主题已编译：{{path}}", { path: o.path ?? key });
  });
  const markReviewed = (path: string) => run("review", async () => {
    await call("POST", "/api/wiki/pages/review", { path, reviewed: true });
    return t("wiki.reviewed_ok", "已标记已审：{{path}}", { path });
  });

  const flagAction = (page: WikiListRow, a: WikiFlagAction) => {
    const target = (a.target || "").trim();
    if (a.id === "open_page") return open(page.path);
    if (a.id === "mark_reviewed") return markReviewed(page.path);
    if (a.id === "compile_theme") {
      const key = themeKey(page);
      return key ? compileTheme(key) : undefined;
    }
    if (a.id === "refresh_dossier") {
      const pid = dossierProjectId(page.path) || projectId;
      if (!pid) return message.error(t("wiki.no_pid", "无法从 path 解析 project_id"));
      return run("dossier-refresh", async () => {
        await call("POST", "/api/wiki/dossier/refresh", { project_id: pid });
        open(page.path);
      });
    }
    if (a.id.startsWith("apply_broken_fix")) {
      const broken = (a.broken || "").trim();
      const replacement = (a.replacement_path || "").trim();
      if (!broken || !replacement) return message.error(t("wiki.fix_incomplete", "断链修复缺 broken / replacement_path"));
      return modal.confirm({
        title: a.label,
        content: t("wiki.fix_confirm", "将修改 {{path}}：[[{{broken}}]] → {{to}}（{{mode}}）", { path: page.path, broken, to: replacement, mode: a.mode || "rewrite" }),
        onOk: () => run("apply-broken", async () => {
          const o = await call<{ wikilink?: string; mode?: string }>("POST", "/api/wiki/lint/apply-broken", { path: page.path, broken, replacement_path: replacement, mode: a.mode || "rewrite" });
          setFlagsOnly(true);
          open(page.path);
          return t("wiki.fix_done", "已修复断链：[[{{broken}}]] → {{to}}（{{mode}}）", { broken, to: o.wikilink || replacement, mode: o.mode || "ok" });
        }),
      });
    }
    // link_from_theme / open_link_candidate* / soft guidance: open the target and surface the hint (never auto-edit L1)
    setSummary(`${a.label}：${a.hint || target || page.path}`);
    open(target || page.path);
  };

  const maintenance: MenuProps["items"] = [
    { key: "lint", label: t("wiki.op_lint", "跑 Lint（stale / conflict / orphan / broken）"), onClick: lint },
    { key: "sweep", label: t("wiki.op_sweep", "清过期 Flag"), onClick: sweep },
    { type: "divider" },
    { key: "fts", label: t("wiki.op_fts", "重建 FTS 索引"), onClick: () => run("fts", async () => {
      const o = await call<{ indexed?: number }>("POST", "/api/wiki/fts/rebuild", {});
      return t("wiki.fts_done", "FTS 已重建：{{n}} 页", { n: o.indexed ?? "?" });
    }) },
    { key: "embed", disabled: embedEnabled === false, label: embedEnabled === false
      ? t("wiki.op_embed_off", "重建 Embed（需开启 wiki_embed_enabled）") : t("wiki.op_embed", "重建 Embed 向量索引"),
    onClick: () => run("embed", async () => {
      const o = await call<{ indexed?: number; embedded_vectors?: number; reason?: string }>("POST", "/api/wiki/embed/rebuild", {});
      return t("wiki.embed_done", "Embed：{{n}} 页 · 向量 {{v}} {{reason}}", { n: o.indexed ?? "?", v: o.embedded_vectors ?? "?", reason: o.reason ?? "" });
    }) },
    { type: "divider" },
    { key: "catalog", label: t("wiki.op_catalog", "重建 Catalog（catalog.md）"), onClick: () => run("catalog", async () => {
      const o = await call<{ entry_count?: number; persisted?: boolean }>("POST", "/api/wiki/catalog/rebuild", { persist: true, limit: 500, project_id: projectId });
      return o.persisted
        ? t("wiki.catalog_saved", "Catalog：{{n}} 条 · 已写 catalog.md", { n: o.entry_count ?? "?" })
        : t("wiki.catalog_unsaved", "Catalog：{{n}} 条 · 未落盘", { n: o.entry_count ?? "?" });
    }) },
    { key: "catalog-dl", label: t("wiki.op_catalog_dl", "下载 Catalog"), onClick: () => run("catalog-dl", async () => {
      await download(wikiApi.catalogPath(projectId), "catalog.md");
    }) },
  ];

  const d = detail.data;
  const meta = dossier.data?.data;
  const revisions = Object.entries(meta?.section_revisions ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const packFlags = Object.entries(meta?.flags ?? {}).filter(([, v]) => v).map(([k]) => k);
  const listLoading = pages.isLoading || (flagsOnly && flags.isLoading) || (searching && search.isFetching);

  return (
    <PageContainer
      subTitle={projectId ? t("wiki.scope", "当前项目的 Wiki（卷宗 / 报告 + 本项目资料编译页）") : undefined}
      extra={[
        embedEnabled === true && <Tag key="embed" color="success">{t("wiki.embed_on", "Embed 已开启")}</Tag>,
        <Link key="graph" to="/knowledge/wiki-graph"><Button icon={<NodeIndexOutlined />}>{t("wiki.graph", "链接图")}</Button></Link>,
        <Button key="dossier" type="primary" disabled={!projectId || !!busy} loading={busy === "dossier-open"} onClick={openDossier}>{t("wiki.dossier", "项目卷宗")}</Button>,
        <Tooltip key="refresh" title={t("wiki.dossier_refresh_tip", "从 live pack 刷新卷宗各节表（确定性；LLM 叙述默认关）")}>
          <Button disabled={!projectId || !!busy} loading={busy === "dossier-refresh"} onClick={refreshDossier}>{t("wiki.dossier_refresh", "刷新卷宗")}</Button>
        </Tooltip>,
        <Dropdown key="ops" disabled={!!busy} menu={{ items: maintenance }}>
          <Button loading={!!busy && !["dossier-open", "dossier-refresh"].includes(busy)}>{t("wiki.maintenance", "维护")} <DownOutlined /></Button>
        </Dropdown>,
      ].filter(Boolean)}
    >
      {!projectId && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("wiki.need_project", "请先在顶栏选择项目：Wiki 按项目隔离，仅显示当前项目关联页")} />}
      {summary && <Alert type="info" showIcon closable style={{ marginBottom: 12 }} message={summary} onClose={() => setSummary(null)} data-testid="wiki-summary" />}
      <ErrorAlert error={pages.error || flags.error} />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card size="small" title={(
            <Space wrap>
              <Select allowClear style={{ width: 130 }} placeholder={t("wiki.all_kinds", "全部类型")} value={kind} onChange={setKind} disabled={flagsOnly}
                options={WIKI_KINDS.map((k) => ({ value: k, label: k }))} />
              <Checkbox checked={flagsOnly} onChange={(e) => setFlagsOnly(e.target.checked)}>{t("wiki.flags_only", "仅 Flag")}</Checkbox>
              <Tooltip title={t("wiki.reviewed_only_tip", "隐藏 flags 含 unreviewed 的草稿")}>
                <Checkbox checked={reviewedOnly} onChange={(e) => setReviewedOnly(e.target.checked)}>{t("wiki.reviewed_only", "仅已审")}</Checkbox>
              </Tooltip>
            </Space>
          )} extra={<Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => void qc.invalidateQueries({ queryKey: ["wiki"] })} />}>
            <Input.Search allowClear value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("wiki.search", "搜索标题 / path / 正文…")}
              style={{ marginBottom: 8 }} suffix={searching && search.data ? <Tag style={{ marginInlineEnd: 0 }}>{search.data.mode === "fts" ? "FTS" : t("wiki.keyword", "关键词")}</Tag> : undefined} />
            <List<WikiListRow>
              size="small" loading={listLoading} dataSource={rows} rowKey={(p) => p.id || p.path}
              pagination={rows.length > 20 ? { pageSize: 20, size: "small", showSizeChanger: false } : false}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={(pages.data?.pages.length ?? 0) === 0
                ? t("wiki.empty", "暂无 Wiki 页（入库后自动编译）") : t("wiki.no_match", "无匹配结果")} /> }}
              renderItem={(p) => (
                <List.Item style={{ background: p.path === selectedPath ? token.controlItemBgActive : undefined, paddingInline: 8, borderRadius: token.borderRadiusSM }}
                  data-testid={`wiki-row-${p.path}`}>
                  <List.Item.Meta
                    title={<Link to={wikiHref(p.path)}>{p.title || p.path}</Link>}
                    description={(
                      <Space direction="vertical" size={4} style={{ width: "100%" }}>
                        <Space wrap size={[4, 4]}>
                          <Tag>{p.kind}</Tag>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.path}</Typography.Text>
                          {(p.flags || []).map((f) => <Tag key={f} color="warning">{f}</Tag>)}
                        </Space>
                        {p.snippet && <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0, fontSize: 12 }}>{p.snippet}</Typography.Paragraph>}
                        {flagsOnly && !!p.actions?.length && (
                          <Space wrap size={4}>
                            {p.actions.map((a) => (
                              <Tooltip key={a.id} title={a.hint}>
                                <Button size="small" disabled={!!busy} onClick={() => flagAction(p, a)}>{a.label}</Button>
                              </Tooltip>
                            ))}
                          </Space>
                        )}
                      </Space>
                    )}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} lg={15}>
          <Card size="small" extra={d && (
            <Space>
              {d.kind === "system" && (
                <Tooltip title={t("wiki.compile_tip", "编译当前体系的 L2 综述（需 wiki_llm_themes_enabled）")}>
                  <Button size="small" disabled={!!busy} loading={busy === "theme"} onClick={() => { const k = themeKey(d); if (k) compileTheme(k); }}>{t("wiki.compile", "编译主题")}</Button>
                </Tooltip>
              )}
              <Button size="small" disabled={!!busy} loading={busy === "review"} onClick={() => markReviewed(d.path)}>{t("wiki.mark_reviewed", "标记已审")}</Button>
            </Space>
          )}>
            {!selectedPath ? (
              <Empty description={t("wiki.pick", "选择左侧页面查看编译记忆；或点「项目卷宗」打开当前项目的 Dossier")} />
            ) : detail.isLoading ? <Skeleton active /> : detail.error ? <ErrorAlert error={detail.error} /> : d ? (
              <>
                {dossierPid && (
                  <Descriptions size="small" bordered column={1} style={{ marginBottom: 12 }} data-testid="wiki-dossier-meta"
                    title={<Space>{t("wiki.dossier_meta", "项目卷宗 · 分节修订")}{(d.flags || []).includes("unreviewed") && <Tag color="warning">{t("wiki.unreviewed", "未审")}</Tag>}</Space>}
                    items={[
                      { key: "rev", label: "section_revisions", children: revisions.length
                        ? <Space wrap size={4}>{revisions.map(([k, v]) => <Tag key={k}>{k}:{v}</Tag>)}</Space>
                        : <Typography.Text type="secondary">{t("wiki.no_revisions", "尚无 sidecar revisions")}</Typography.Text> },
                      ...(packFlags.length ? [{ key: "flags", label: "flags", children: <Space wrap size={4}>{packFlags.map((k) => <Tag key={k} color="warning">{k}</Tag>)}</Space> }] : []),
                    ]} />
                )}
                <WikiReader page={d} linkPages={linkPages} />
              </>
            ) : null}
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
}
