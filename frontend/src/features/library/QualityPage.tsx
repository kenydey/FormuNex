import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { Alert, Button, Space, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { qualityTone } from "./logic";
import type { KbQualityOps } from "./types";

/** Read-only knowledge-base quality ops: scan pressure, topicality shadow calibration, gate counters, heuristic score. */
export default function QualityPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const q = useQuery({
    queryKey: ["kb", "quality-ops", projectId],
    queryFn: () => call<KbQualityOps>("GET", projectId ? `/api/kb/quality-ops?project_id=${encodeURIComponent(projectId)}` : "/api/kb/quality-ops"),
  });
  const d = q.data;
  const lat = d?.hybrid_search_latency;
  const stats: { title: string; value: unknown; warn?: boolean }[] = d ? [
    { title: t("library.quality.active", "活跃源"), value: d.sources_active },
    { title: t("library.quality.archived", "已归档"), value: d.sources_archived },
    { title: t("library.quality.scan", "scan 压力"), value: typeof d.scan_pressure === "number" ? d.scan_pressure.toFixed(3) : d.scan_pressure, warn: d.scan_near_cap },
    { title: t("library.quality.vector", "向量模式"), value: d.vector_mode },
    { title: "embedding", value: d.embedding_model ? String(d.embedding_model).split("/").pop() : null },
    { title: "hybrid p95", value: lat?.p95_ms == null ? null : `${lat.p95_ms}ms`, warn: !!lat?.ann_last },
    { title: "ANN matrix", value: lat?.ann_matrix_last ? `on · streak ${lat.ann_streak ?? 0}` : lat?.ann_last ? "prefilter" : "off", warn: !!lat?.ann_matrix_last },
    { title: t("library.quality.reject", "主题拒收 %"), value: d.topicality_would_reject_pct },
    { title: t("library.quality.embedded", "嵌入块"), value: d.embedded_chunks },
    { title: t("library.quality.chunks", "活跃块"), value: d.chunks_active },
    { title: t("library.quality.shadow", "shadow 批次"), value: (d.relevance_shadow as { batch_count?: number } | undefined)?.batch_count },
  ] : [];
  const tone = qualityTone(d?.kb_quality_score);

  return (
    <PageContainer subTitle={t("library.quality.sub", "只读聚合：scan 压力 · 主题影子校准 · 门禁计数 · 启发式评分（不改变入库行为）")}
      extra={<Button onClick={() => void q.refetch()} loading={q.isFetching}>{t("library.quality.reload", "刷新")}</Button>}>
      <ErrorAlert error={q.error} />
      {q.isLoading && <Spin />}
      {d && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <ProCard bordered>
            <Space align="baseline" size="large">
              <Typography.Title level={1} type={tone} style={{ margin: 0 }}>{d.kb_quality_score == null ? "—" : d.kb_quality_score.toFixed(0)}</Typography.Title>
              <Typography.Text type="secondary">{t("library.quality.score", "知识库质量分")}</Typography.Text>
              <Space wrap>
                {Object.entries(d.kb_quality_components ?? {}).map(([k, v]) => <Tag key={k}>{k} {Number(v).toFixed(1)}</Tag>)}
              </Space>
            </Space>
          </ProCard>
          {d.scan_near_cap && (
            <Alert type="warning" showIcon message={t("library.quality.near_cap", "scan 接近上限")}
              description={t("library.quality.near_cap_hint", "活跃切块接近 kb_search_scan_limit。建议：在知识源页归档低质文档，再在依赖管理中 dry-run 保留期清理（需确认，默认不物理删除）。")}
              action={<Space direction="vertical">
                <Button size="small" onClick={() => nav("/knowledge/sources")}>{t("library.quality.to_sources", "打开知识源归档")}</Button>
                <Button size="small" onClick={() => nav("/knowledge/quality?tab=probe")}>{t("library.quality.to_probe", "查看检索探针")}</Button>
              </Space>} />
          )}
          <StatisticCard.Group direction="row" style={{ flexWrap: "wrap" }}>
            {stats.map((s) => (
              <StatisticCard key={s.title} style={{ minWidth: 150 }}
                statistic={{ title: s.title, value: s.value == null || s.value === "" ? "—" : String(s.value), status: s.warn ? "warning" : undefined }} />
            ))}
          </StatisticCard.Group>
          {d.notes?.length ? <ul>{d.notes.map((n) => <li key={n}><Typography.Text type="secondary">{n}</Typography.Text></li>)}</ul> : null}
        </Space>
      )}
    </PageContainer>
  );
}
