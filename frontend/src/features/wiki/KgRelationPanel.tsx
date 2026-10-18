import { Alert, Button, Collapse, Empty, Input, List, Select, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import { isFlagGateError, loadKgBundle } from "./api";
import { KgFeedbackStatsStrip } from "./KgFeedbackStatsStrip";
import { useDebounced } from "./rows";
import type { KgFeedbackReport, KgPath, KgRelation } from "./types";

const REL_ZH: Record<string, string> = { substitutes: "替代", synergizes: "协同", inhibits: "抑制", correlates_pos: "正相关", correlates_neg: "负相关", requires: "依赖" };
const METHOD_COLOR: Record<string, string> = { measured: "gold", llm: "purple", vision_table: "cyan" };

export const relationMethod = (r: KgRelation) => (r.extraction_method || r.evidence?.[0]?.extraction_method || "rule").toLowerCase();

function useRelLabel() {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    substitutes: t("wiki.kg_rel_substitutes", "替代"), synergizes: t("wiki.kg_rel_synergizes", "协同"), inhibits: t("wiki.kg_rel_inhibits", "抑制"),
    correlates_pos: t("wiki.kg_rel_correlates_pos", "正相关"), correlates_neg: t("wiki.kg_rel_correlates_neg", "负相关"), requires: t("wiki.kg_rel_requires", "依赖"),
  };
  return (type: string) => labels[type] ?? REL_ZH[type] ?? type;
}

function RelationItem({ rel }: { rel: KgRelation }) {
  const { t } = useTranslation();
  const relLabel = useRelLabel();
  const ev = rel.evidence?.[0];
  const method = relationMethod(rel);
  const campaign = /^measured:campaign_(\d+)$/.exec(ev?.source_id || "")?.[1];
  return (
    <List.Item>
      <Space direction="vertical" size={2} style={{ width: "100%" }}>
        <Space wrap size={4}>
          <Tag color="geekblue">{relLabel(rel.relation_type)}</Tag>
          <Tag color={METHOD_COLOR[method]}>{method === "measured" ? t("wiki.kg_m_measured", "实测") : method === "vision_table" ? t("wiki.kg_m_table", "表格") : method}</Tag>
          {campaign && <Tag color="gold">{t("wiki.kg_campaign", "台账#{{id}}", { id: campaign })}</Tag>}
          <Typography.Text type="secondary" style={{ fontFamily: "monospace", fontSize: 12 }}>
            {rel.source_entity_id.slice(0, 18)} → {rel.target_entity_id.slice(0, 18)}
          </Typography.Text>
        </Space>
        {ev?.sentence && <Typography.Text type="secondary" italic ellipsis={{ tooltip: ev.sentence }} style={{ fontSize: 12 }}>“{ev.sentence}”</Typography.Text>}
      </Space>
    </List.Item>
  );
}

/**
 * Knowledge-graph relations of an entity name: resolve → semantic relations (method filter), substitutes, measured
 * contradictions, shortest path between two entities. ``hideWhenEmpty`` renders nothing when no entity resolves.
 */
export function KgRelationPanel({ query, hideWhenEmpty = true, defaultExpanded = false }: { query: string; hideWhenEmpty?: boolean; defaultExpanded?: boolean }) {
  const { t } = useTranslation();
  const relLabel = useRelLabel();
  const q = useDebounced(query.trim(), 600);
  const [method, setMethod] = useState("all");
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const report = useQuery({ queryKey: ["kg", "feedback-report"], queryFn: () => call<KgFeedbackReport>("GET", "/api/kg/feedback/report"), retry: false });
  const bundle = useQuery({ queryKey: ["kg", "bundle", q], queryFn: () => loadKgBundle(q), enabled: q.length >= 2, retry: false });
  const path = useMutation({
    mutationFn: () => call<KgPath>("GET", `/api/kg/path?${new URLSearchParams({ src: src.trim(), dst: dst.trim(), max_depth: "4" })}`),
  });

  // KG disabled (409 / 知识图谱未启用) is not an error worth showing next to an entity.
  const error = bundle.error && !isFlagGateError(bundle.error, "kg_enabled", "知识图谱未启用") && !errorText(bundle.error).includes("409") ? bundle.error : null;
  const resolved = bundle.data?.resolved;
  const hasEntity = !!resolved && (resolved.chemicals.length > 0 || resolved.trade_products.length > 0);
  const loading = q.length >= 2 && bundle.isFetching;
  if (hideWhenEmpty && !loading && !error && !hasEntity) return null;

  const relations = bundle.data?.relations?.length ? bundle.data.relations : resolved?.top_relations ?? [];
  const shown = method === "all" ? relations : relations.filter((r) => relationMethod(r) === method);
  const measured = relations.filter((r) => relationMethod(r) === "measured").length;
  const subs = bundle.data?.substitutes?.substitutes ?? [];
  const contra = bundle.data?.contradictions?.contradictions ?? [];
  const primary = resolved?.chemicals[0]?.canonical_name ?? resolved?.trade_products[0]?.trade_name ?? q;
  const primaryId = resolved?.chemicals[0]?.id;

  const summary = (
    <Space wrap size={4}>
      <Typography.Text strong>{t("wiki.kg_panel_title", "知识图谱关系")}</Typography.Text>
      {loading && <Spin size="small" />}
      {hasEntity && <Typography.Text type="secondary">{t("wiki.kg_summary", "{{name}}：{{n}} 条语义关系", { name: primary, n: relations.length })}</Typography.Text>}
      {measured > 0 && <Tag color="gold">{t("wiki.kg_measured_n", "{{n}} 条实测", { n: measured })}</Tag>}
      {subs.length > 0 && <Tag color="cyan">{t("wiki.kg_subs_n", "{{n}} 个替代候选", { n: subs.length })}</Tag>}
      {contra.length > 0 && <Tag color="error">{t("wiki.kg_contra_n", "{{n}} 处矛盾", { n: contra.length })}</Tag>}
    </Space>
  );

  const body = (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      {report.data?.alert && <Alert type="warning" showIcon message={report.data.alert} />}
      <KgFeedbackStatsStrip compact />
      {error && <Alert type="error" showIcon message={errorText(error)} />}
      {!hasEntity && !loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("wiki.kg_no_entity", "未解析到实体")} />}
      {hasEntity && (
        <>
          <div>
            {t("wiki.kg_entity", "实体")}：<Typography.Text strong>{primary}</Typography.Text>
            {resolved?.chemicals[0]?.cas_no && <Tag style={{ marginInlineStart: 8 }}>{resolved.chemicals[0].cas_no}</Tag>}
          </div>
          <div>
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Typography.Text strong>{t("wiki.kg_relations", "语义关系 ({{n}}/{{total}})", { n: shown.length, total: relations.length })}</Typography.Text>
              <Select size="small" value={method} onChange={setMethod} style={{ width: 120 }} options={[
                { value: "all", label: t("wiki.kg_m_all", "全部") }, { value: "measured", label: t("wiki.kg_m_measured", "实测") },
                { value: "rule", label: t("wiki.kg_m_rule", "文献(rule)") }, { value: "llm", label: "LLM" }]} />
            </Space>
            <List size="small" dataSource={shown} rowKey="id" style={{ maxHeight: 260, overflow: "auto" }}
              locale={{ emptyText: t("wiki.kg_no_relations", "暂无已抽取的语义关系（需开启 KG 关系抽取并重建链接）") }}
              renderItem={(r) => <RelationItem rel={r} />} />
          </div>
          {subs.length > 0 && (
            <div>
              <Typography.Text strong>{t("wiki.kg_substitutes", "替代品发现")}</Typography.Text>
              <List size="small" dataSource={subs} rowKey="entity_id" renderItem={(c) => (
                <List.Item>
                  <Space wrap size={4}>
                    <Typography.Text>{c.entity_name || c.entity_id}</Typography.Text>
                    <Typography.Text type="secondary">({relLabel(c.relation_type)}, {Math.round(c.confidence * 100)}%)</Typography.Text>
                    {c.contradiction_flag && (
                      <Tooltip title={c.contradiction_detail || t("wiki.kg_contra_tip", "文献关系与实测矛盾")}><Tag color="error">{t("wiki.kg_refuted", "实测反驳")}</Tag></Tooltip>
                    )}
                  </Space>
                </List.Item>
              )} />
            </div>
          )}
          {contra.length > 0 && (
            <div>
              <Typography.Text strong>{t("wiki.kg_contradictions", "文献 × 实测矛盾")}</Typography.Text>
              <List size="small" dataSource={contra} rowKey={(c) => `${c.target_entity_id}-${c.literature_relation}-${c.measured_property ?? ""}`} renderItem={(c) => (
                <List.Item>
                  <Space wrap size={4}>
                    <Typography.Text>{c.target_entity_name || c.target_entity_id}</Typography.Text>
                    <Tag>{relLabel(c.literature_relation)}</Tag>
                    {c.measured_property && <Typography.Text type="secondary">{c.measured_property}{c.measured_value != null ? ` = ${c.measured_value}` : ""}</Typography.Text>}
                    {c.recommended_action && <Typography.Text type="secondary">→ {c.recommended_action}</Typography.Text>}
                  </Space>
                </List.Item>
              )} />
            </div>
          )}
        </>
      )}
      <div data-testid="kg-path">
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <Typography.Text strong>{t("wiki.kg_path", "实体路径")}</Typography.Text>
          {primaryId && <Button size="small" type="link" onClick={() => setSrc(primaryId)}>{t("wiki.kg_use_as_src", "用当前实体作起点")}</Button>}
        </Space>
        <Space.Compact style={{ width: "100%", marginTop: 4 }}>
          <Input size="small" value={src} onChange={(e) => setSrc(e.target.value)} placeholder={t("wiki.kg_src", "起点 entity id")} />
          <Input size="small" value={dst} onChange={(e) => setDst(e.target.value)} placeholder={t("wiki.kg_dst", "终点 entity id")} />
          <Button size="small" disabled={!src.trim() || !dst.trim()} loading={path.isPending} onClick={() => path.mutate()}>{t("wiki.kg_find_path", "寻路")}</Button>
        </Space.Compact>
        {path.error && <Alert style={{ marginTop: 8 }} type="error" showIcon message={errorText(path.error)} />}
        {path.data && (path.data.found ? (
          <div style={{ marginTop: 8 }}>
            <Typography.Text>{t("wiki.kg_path_found", "找到路径 · {{n}} 跳", { n: path.data.hops })}</Typography.Text>
            <ol style={{ margin: "4px 0 0", paddingInlineStart: 20 }}>
              {path.data.steps.map((s, i) => (
                <li key={`${s.entity_id}-${i}`}><Typography.Text type="secondary">{s.relation?.relation_type ?? "?"} → {s.entity_name || s.entity_id}</Typography.Text></li>
              ))}
            </ol>
          </div>
        ) : <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>{t("wiki.kg_path_none", "未找到路径（关系层可能为空）")}</Typography.Paragraph>)}
      </div>
    </Space>
  );

  return <Collapse size="small" defaultActiveKey={defaultExpanded ? ["kg"] : []} items={[{ key: "kg", label: summary, children: body }]} />;
}
