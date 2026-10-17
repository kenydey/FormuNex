import { ReloadOutlined } from "@ant-design/icons";
import { PageContainer } from "@ant-design/pro-components";
import { FlagLink } from "./FlagLink";
import { Alert, App, Button, Card, Col, Input, Row, Segmented, Select, Space, Switch, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { envFlag, isFlagGateError, wikiApi } from "./api";
import { filterGraph, WIKI_KINDS, type GraphColorMode } from "./graph";
import { GraphCanvas } from "./GraphCanvas";
import type { WikiLintResult, WikiPageGraphResponse } from "./types";
import { WikiGraphInsights } from "./WikiGraphInsights";
import { WikiReaderDrawer } from "./WikiReaderDrawer";

const FLAG = "wiki_page_graph_enabled";

/** Wiki 链接图 (legacy HubWikiGraphPane + WikiPageGraphCanvas + HubWikiGraphInsights): page [[wikilink]] graph, not the materials KG. */
export default function WikiGraphPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [kind, setKind] = useState<string | undefined>();
  const [hideOrphan, setHideOrphan] = useState(false);
  const [colorMode, setColorMode] = useState<GraphColorMode>("kind");
  const [query, setQuery] = useState("");
  const [openPath, setOpenPath] = useState<string | null>(null);

  const env = useQuery({ queryKey: ["settings", "env-flags"], queryFn: wikiApi.envFlags, retry: false });
  const flagOn = env.data ? envFlag(env.data.flags, FLAG) === true : null;
  const graph = useQuery({
    queryKey: ["wiki", "graph", projectId, kind ?? ""], enabled: !!projectId && flagOn === true,
    queryFn: () => call<WikiPageGraphResponse>("GET", `/api/wiki/graph?${new URLSearchParams({ limit: "400", include_orphan: "true", project_id: projectId!, ...(kind ? { kinds: kind } : {}) })}`),
  });
  const filtered = useMemo(() => filterGraph(graph.data?.nodes ?? [], graph.data?.edges ?? [], { query, hideOrphan, kinds: kind ? [kind] : undefined }),
    [graph.data, query, hideOrphan, kind]);
  const linkPages = useMemo(() => (graph.data?.nodes ?? []).map((n) => ({ path: n.path, title: n.label, kind: n.kind })), [graph.data]);
  const pathById = useMemo(() => new Map((graph.data?.nodes ?? []).map((n) => [n.id || n.path, n.path])), [graph.data]);

  const op = useMutation({
    mutationFn: async (label: "lint" | "dossier") => {
      if (label === "lint") {
        const o = await call<WikiLintResult>("POST", "/api/wiki/lint/run", { limit: 200, detect_orphan: true });
        return t("wiki.lint_summary_short", "Lint：扫描 {{s}} · 有旗标 {{f}} · 孤儿 {{o}}", { s: o.scanned ?? "?", f: o.flagged ?? "?", o: o.orphan_count ?? "?" });
      }
      const o = await call<{ path?: string }>("POST", "/api/wiki/dossier/refresh", { project_id: projectId });
      if (o.path) setOpenPath(o.path);
      return t("wiki.dossier_refreshed", "卷宗已刷新：{{path}}", { path: o.path ?? "—" });
    },
    onSuccess: (msg) => {
      message.success(msg);
      void qc.invalidateQueries({ queryKey: ["wiki"] });
    },
    onError: (e) => message.error(errorText(e)),
  });

  const gated = flagOn === false || isFlagGateError(graph.error, FLAG, "wiki_enabled") || !!env.error;
  const m = graph.data?.meta;
  return (
    <PageContainer subTitle={t("wiki.graph_sub", "Wiki 页链接图（[[wikilink]]）· 非配方 / 材料 KG · 点节点打开阅读")}>
      {!projectId && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("wiki.graph_need_project", "请先在顶栏选择项目（页图按项目过滤）")} />}
      {gated && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} data-testid="wiki-graph-flag"
          message={t("wiki.graph_flag", "页图未启用或旗标读取失败：请在系统设置的环境开关中打开 wiki_page_graph_enabled（依赖 wiki_enabled）")}
          description={env.error ? errorText(env.error) : undefined} action={<FlagLink flag={FLAG} />} />
      )}
      {!gated && <ErrorAlert error={graph.error} />}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear style={{ width: 140 }} placeholder={t("wiki.all_kinds", "全部类型")} value={kind} onChange={setKind}
            options={WIKI_KINDS.map((k) => ({ value: k, label: k }))} />
          <Space size={4}><Switch size="small" checked={hideOrphan} onChange={setHideOrphan} />{t("wiki.hide_orphan", "隐藏孤立")}</Space>
          <Segmented value={colorMode} onChange={(v) => setColorMode(v as GraphColorMode)} options={[
            { value: "kind", label: t("wiki.color_kind", "按类型着色") }, { value: "community", label: t("wiki.color_community", "按社区着色") }]} />
          <Input.Search allowClear style={{ width: 220 }} placeholder={t("wiki.graph_search", "搜索标题 / path…")} value={query} onChange={(e) => setQuery(e.target.value)} />
          <Button icon={<ReloadOutlined />} disabled={flagOn !== true || !projectId} loading={graph.isFetching} onClick={() => void graph.refetch()}>{t("wiki.refresh", "刷新")}</Button>
        </Space>
        {m && (
          <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }} data-testid="wiki-graph-meta">
            {t("wiki.graph_meta", "节点 {{n}}/{{N}} · 边 {{e}}/{{E}}", { n: filtered.nodes.length, N: m.node_count, e: filtered.edges.length, E: m.edge_count })}
            {m.broken_links != null && t("wiki.meta_broken", " · 断链 {{n}}", { n: m.broken_links })}
            {m.orphan_count != null && t("wiki.meta_orphan", " · 孤立 {{n}}", { n: m.orphan_count })}
            {m.community_count != null && t("wiki.meta_community", " · 社区 {{n}}", { n: m.community_count })}
            {m.weighting && t("wiki.meta_weighting", " · 边权 {{w}}", { w: m.weighting })}
            {m.elapsed_ms != null && ` · ${m.elapsed_ms}ms`}
            {m.truncated && t("wiki.meta_truncated", " · 已截断")}
          </Typography.Paragraph>
        )}
      </Card>
      {flagOn === true && projectId && (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={17}>
            <Card size="small" loading={graph.isLoading}>
              <GraphCanvas nodes={filtered.nodes} edges={filtered.edges} colorMode={colorMode}
                onSelect={(id) => setOpenPath(pathById.get(id) ?? id)}
                emptyText={t("wiki.graph_empty", "无链接可显示：可跑 Lint 或编译更多带 [[wikilink]] 的页")} />
            </Card>
          </Col>
          <Col xs={24} lg={7}>
            <WikiGraphInsights insights={graph.data?.insights} busy={op.isPending ? op.variables ?? null : null} canRefreshDossier={!!projectId}
              onOpenPath={setOpenPath} onRunLint={() => op.mutate("lint")} onRefreshDossier={() => op.mutate("dossier")} />
          </Col>
        </Row>
      )}
      <WikiReaderDrawer path={openPath} onClose={() => setOpenPath(null)} onNavigate={setOpenPath} linkPages={linkPages} />
    </PageContainer>
  );
}
