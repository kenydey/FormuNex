import { ReloadOutlined } from "@ant-design/icons";
import { PageContainer } from "@ant-design/pro-components";
import { Alert, Button, Card, Col, Descriptions, Empty, Input, Row, Space, Table, Tabs, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { isFlagGateError } from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { KgFeedbackStatsStrip } from "./KgFeedbackStatsStrip";
import { KgRelationPanel } from "./KgRelationPanel";
import type { KgGraphNode, KgGraphResponse, KgStats, Neo4jCompound, Neo4jStats } from "./types";

const DEFAULT_TYPES = "substitutes,measured_*";

function CountTags({ counts }: { counts?: Record<string, number> }) {
  const entries = Object.entries(counts ?? {});
  if (!entries.length) return <>—</>;
  return <Space wrap size={4}>{entries.map(([k, v]) => <Tag key={k}>{k}: {v}</Tag>)}</Space>;
}

/** 材料知识图谱 (legacy HubGraphPane): SQLite KG link canvas + entity relations, and KG / Neo4j statistics. */
export default function KgGraphPage() {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  const [tab, setTab] = useState("canvas");
  const [typesDraft, setTypesDraft] = useState(DEFAULT_TYPES);
  const [types, setTypes] = useState(DEFAULT_TYPES);
  const [selected, setSelected] = useState<KgGraphNode | null>(null);

  const graph = useQuery({
    queryKey: ["kg", "graph", types], enabled: !!projectId && tab === "canvas",
    queryFn: () => call<KgGraphResponse>("GET", `/api/kg/graph?${new URLSearchParams({ relation_types: types || DEFAULT_TYPES, limit: "400" })}`),
  });
  const stats = useQuery({ queryKey: ["kg", "stats"], queryFn: () => call<KgStats>("GET", "/api/kg/stats"), enabled: !!projectId && tab === "stats", retry: false });
  const neo = useQuery({ queryKey: ["kg", "neo4j", "stats"], queryFn: () => call<Neo4jStats>("GET", "/api/kg/neo4j/stats"), enabled: !!projectId && tab === "stats", retry: false });
  const compounds = useQuery({
    queryKey: ["kg", "neo4j", "compounds"], enabled: !!projectId && tab === "stats" && !!neo.data?.reachable, retry: false,
    queryFn: () => call<Neo4jCompound[]>("GET", "/api/kg/neo4j/compounds?limit=40"),
  });
  const kgOff = isFlagGateError(graph.error || stats.error, "kg_enabled", "知识图谱未启用");
  const m = graph.data?.meta;
  const nodes = graph.data?.nodes ?? [];
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const commit = () => setTypes(typesDraft.trim() || DEFAULT_TYPES);
  const refetch = () => void (tab === "canvas" ? graph.refetch() : Promise.all([stats.refetch(), neo.refetch()]));

  return (
    <PageContainer
      subTitle={t("wiki.kg_sub", "材料关系（配方 KG · SQLite）· 全局语料预览（尚未按项目切分）· 与 Wiki 链接图分离")}
      extra={<Button icon={<ReloadOutlined />} disabled={!projectId} loading={graph.isFetching || stats.isFetching} onClick={refetch}>{t("wiki.refresh", "刷新")}</Button>}
    >
      {!projectId ? (
        <Alert type="warning" showIcon message={t("wiki.kg_need_project", "请先在顶栏选择项目后再查看材料图谱（只读）")} />
      ) : (
        <>
          {kgOff && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("wiki.kg_off", "知识图谱未启用：请在系统设置的环境开关中打开 kg_enabled")} />}
          <Tabs activeKey={tab} onChange={setTab} items={[
            { key: "canvas", label: t("wiki.kg_canvas", "画布"), children: (
              <>
                {!kgOff && <ErrorAlert error={graph.error} />}
                <Card size="small" style={{ marginBottom: 16 }}>
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <KgFeedbackStatsStrip compact />
                    <Space wrap>
                      <Typography.Text>{t("wiki.kg_types", "关系类型")}</Typography.Text>
                      <Input style={{ width: 280, fontFamily: "monospace" }} value={typesDraft} onChange={(e) => setTypesDraft(e.target.value)}
                        onBlur={commit} onPressEnter={commit} title={t("wiki.kg_types_tip", "逗号分隔；支持 measured_*")} />
                    </Space>
                    {m && (
                      <Typography.Text type="secondary" data-testid="kg-graph-meta">
                        {t("wiki.kg_meta", "节点 {{n}} · 边 {{e}}", { n: m.node_count, e: m.edge_count })}
                        {m.scanned_links != null && t("wiki.kg_meta_scanned", " · 扫描链接 {{n}}", { n: m.scanned_links })}
                        {m.elapsed_ms != null && ` · ${m.elapsed_ms}ms`}
                        {m.truncated && t("wiki.meta_truncated", " · 已截断")}
                        {m.backend && ` · ${m.backend}`}
                        {m.relation_types?.length ? ` · [${m.relation_types.join(", ")}]` : ""}
                      </Typography.Text>
                    )}
                  </Space>
                </Card>
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={16}>
                    <Card size="small" loading={graph.isLoading}>
                      <GraphCanvas nodes={nodes} edges={graph.data?.edges ?? []} directed={false} onSelect={(id) => setSelected(byId.get(id) ?? null)}
                        emptyText={t("wiki.kg_empty", "无材料关系边：可重建关系层或放宽关系类型")} />
                    </Card>
                  </Col>
                  <Col xs={24} lg={8}>
                    <Card size="small" title={t("wiki.kg_entity_panel", "实体关系")} data-testid="kg-entity-panel">
                      {selected ? (
                        <Space direction="vertical" style={{ width: "100%" }}>
                          <Descriptions size="small" column={1} items={[
                            { key: "label", label: t("wiki.kg_name", "名称"), children: selected.label },
                            { key: "id", label: "ID", children: <Typography.Text code copyable>{selected.id}</Typography.Text> },
                            { key: "kind", label: t("wiki.kg_kind", "类型"), children: <Tag>{selected.kind}</Tag> },
                            { key: "deg", label: t("wiki.kg_degree", "度"), children: selected.degree ?? 0 },
                          ]} />
                          <KgRelationPanel key={selected.id} query={selected.label || selected.id} hideWhenEmpty={false} defaultExpanded />
                        </Space>
                      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("wiki.kg_pick", "点击画布节点查看替代 / 矛盾关系")} />}
                    </Card>
                  </Col>
                </Row>
              </>
            ) },
            { key: "stats", label: t("wiki.kg_stats", "统计"), children: (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Card size="small" title={t("wiki.kg_sqlite", "SQLite 知识图谱（全局探针）")} loading={stats.isLoading}>
                  {stats.data ? (
                    <>
                      <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} items={[
                        { key: "en", label: t("wiki.kg_enabled", "已启用"), children: stats.data.enabled ? t("common.yes", "是") : t("common.no", "否") },
                        { key: "ent", label: t("wiki.kg_entities", "实体"), children: stats.data.entities },
                        { key: "men", label: t("wiki.kg_mentions", "提及"), children: stats.data.mentions },
                        { key: "lnk", label: t("wiki.kg_links", "链接"), children: stats.data.links },
                        { key: "bk", label: t("wiki.kg_by_kind", "按实体类型"), span: 2, children: <CountTags counts={stats.data.entities_by_kind} /> },
                        { key: "bt", label: t("wiki.kg_by_type", "按关系类型"), span: 2, children: <CountTags counts={stats.data.links_by_type} /> },
                      ]} />
                      {stats.data.relation_layer_empty && <Alert style={{ marginTop: 12 }} type="warning" showIcon message={t("wiki.kg_layer_empty", "已有实体但关系层为空：需重建实体链接")} />}
                      {stats.data.warnings?.map((w) => <Alert key={w} style={{ marginTop: 8 }} type="warning" showIcon message={w} />)}
                    </>
                  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("wiki.kg_stats_none", "无法读取 /api/kg/stats（可能未启用 kg_enabled）")} />}
                </Card>
                <Card size="small" title={t("wiki.kg_neo4j", "Neo4j（可选 · 全局 · 非画布依赖）")} loading={neo.isLoading}>
                  {neo.data ? (
                    <>
                      <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} items={[
                        { key: "en", label: t("wiki.kg_enabled", "已启用"), children: neo.data.enabled ? t("common.yes", "是") : t("common.no", "否") },
                        { key: "re", label: t("wiki.kg_reachable", "可连接"), children: neo.data.reachable ? t("common.yes", "是") : t("common.no", "否") },
                        { key: "st", label: t("wiki.kg_counts", "计数"), span: 2, children: <CountTags counts={neo.data.stats} /> },
                      ]} />
                      {!!compounds.data?.length && (
                        <Table<Neo4jCompound> style={{ marginTop: 12 }} size="small" rowKey="uid" dataSource={compounds.data} pagination={{ pageSize: 10 }} columns={[
                          { title: t("wiki.kg_name", "名称"), dataIndex: "name", render: (v) => v || "—" },
                          { title: "CAS", dataIndex: "cas_number", render: (v) => v || "—" },
                          { title: "uid", dataIndex: "uid", render: (v) => <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>{v}</Typography.Text> },
                        ]} />
                      )}
                    </>
                  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("wiki.kg_neo4j_none", "Neo4j 未连接或不可用（画布仍可用 SQLite KG）")} />}
                </Card>
              </Space>
            ) },
          ]} />
        </>
      )}
    </PageContainer>
  );
}
