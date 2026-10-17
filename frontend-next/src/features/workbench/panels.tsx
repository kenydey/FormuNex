import { ProCard } from "@ant-design/pro-components";
import { Alert, Button, InputNumber, List, Space, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { BiasTrendView } from "./charts";
import { useTrainingStatus } from "./hooks";
import { summarizeHits, type MeasuredMetricHit } from "./measuredHits";
import type { AdaptiveMeta, BiasTrend, DoePlan } from "./types";

/** 训练数据不足横幅 (legacy ``TrainingDataBanner``): optimisation runs on priors until enough measured records exist. */
export function TrainingDataBanner() {
  const { t } = useTranslation();
  const q = useTrainingStatus();
  const [closed, setClosed] = useState(false);
  const st = q.data;
  if (closed || !st || st.sufficient) return null;
  return (
    <Alert type="warning" showIcon closable onClose={() => setClosed(true)} style={{ marginBottom: 16 }}
      message={t("workbench.training.short", "训练数据不足：当前 {{n}} 条实测记录 < 起训阈值 {{min}} 条", { n: st.total_records, min: st.min_samples })}
      description={st.message || t("workbench.training.hint", "寻优结果基于预测器先验；在实验台账回填实测值（或导入实测 CSV）后才会转为数据驱动。")}
      action={<Button size="small" icon={<ReloadOutlined />} loading={q.isFetching} onClick={() => void q.refetch()}>{t("workbench.refresh", "刷新")}</Button>} />
  );
}

/** 主动学习 DOE 解读 (legacy ``AdaptiveDoeInsights``): strategy, next action, anomalies, why each AI run was picked. */
export function AdaptiveDoeInsights({ meta, plan }: { meta: AdaptiveMeta | null | undefined; plan?: DoePlan | null }) {
  const { t } = useTranslation();
  if (!meta) return null;
  const expl = meta.run_explanations ?? [];
  const anomalies = meta.anomalies ?? [];
  if (!meta.strategy_rationale && !meta.recommended_next_action && !expl.length && !anomalies.length) return null;
  const label = (k?: string) => {
    if (!k) return "";
    if (k === "exploration") return t("workbench.adaptive.exploration", "探索为主");
    if (k === "balanced") return t("workbench.adaptive.balanced", "探索 + 利用");
    if (k === "exploitation") return t("workbench.adaptive.exploitation", "利用为主");
    return k;
  };
  const aiRuns = (plan?.runs ?? []).filter((r) => r.ai_suggested).map((r) => ({ run: r, e: expl.find((x) => x.run_id === r.run_id) })).filter((x) => x.e);
  return (
    <ProCard bordered size="small" title={t("workbench.adaptive.title", "主动学习 DOE 解读")} style={{ marginBottom: 12 }}>
      {meta.strategy_label && <Tag color="purple">{t("workbench.adaptive.strategy", "策略：{{s}}", { s: label(meta.strategy_label) })}</Tag>}
      {meta.budget_remaining != null && <Tag>{t("workbench.adaptive.budget", "剩余预算 {{n}}", { n: meta.budget_remaining })}</Tag>}
      {meta.strategy_rationale && <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>{meta.strategy_rationale}</Typography.Paragraph>}
      {meta.recommended_next_action && <Alert type="info" showIcon style={{ marginBottom: 8 }}
        message={t("workbench.adaptive.next", "下一步：{{a}}", { a: meta.recommended_next_action })} />}
      {anomalies.length > 0 && (
        <List size="small" header={<Typography.Text type="warning">{t("workbench.adaptive.anomalies", "异常实验点（{{n}}）", { n: anomalies.length })}</Typography.Text>}
          dataSource={anomalies.slice(0, 5)} rowKey={(a) => `${a.experiment_id}-${a.type}`}
          renderItem={(a) => <List.Item><Space><code>{a.experiment_id}</code><Tag>{a.type}</Tag>{a.note}</Space></List.Item>} />
      )}
      {aiRuns.length > 0 && (
        <List size="small" header={<Typography.Text strong>{t("workbench.adaptive.explain", "推荐解释")}</Typography.Text>}
          dataSource={aiRuns} rowKey={(x) => String(x.run.run_id)}
          renderItem={(x) => (
            <List.Item>
              <div>
                <Tag color="purple">#{x.run.run_id}</Tag>{x.e!.summary}
                {x.e!.constraint_warnings?.[0] && <div><Typography.Text type="warning">⚠ {x.e!.constraint_warnings[0]}</Typography.Text></div>}
              </div>
            </List.Item>
          )} />
      )}
    </ProCard>
  );
}

/** 实测命中提示 (legacy ``MeasuredMetricHitsBanner``) for recommendation / leaderboard cards. */
export function MeasuredMetricHitsBanner({ hits }: { hits: MeasuredMetricHit[] | null | undefined }) {
  const { t } = useTranslation();
  const s = summarizeHits(hits);
  if (!s) return null;
  const mats = s.materials.join("、") || t("workbench.hits.materials", "材料");
  const mets = s.metrics.join("/") || t("workbench.hits.metrics", "指标");
  const text = s.quality === "good" ? t("workbench.hits.good", "目标指标实测加成：{{m}}（{{k}}）", { m: mats, k: mets })
    : s.quality === "poor" ? t("workbench.hits.poor", "目标指标实测偏弱降权：{{m}}（{{k}}）", { m: mats, k: mets })
      : t("workbench.hits.presence", "目标指标实测存在：{{m}}（{{k}}）", { m: mats, k: mets });
  return <Tag data-testid="measured-metric-hits" color={s.quality === "good" ? "success" : s.quality === "poor" ? "warning" : "processing"}>{text}</Tag>;
}

/** 预测偏差趋势卡 (legacy ``BiasTrendPanel``) — GET /api/experiments/workbench/{id}/bias-trend. */
export function BiasTrendCard({ campaignId, extra }: { campaignId: number | null | undefined; extra?: React.ReactNode }) {
  const { t } = useTranslation();
  const [threshold, setThreshold] = useState(50);
  const q = useQuery({ queryKey: ["workbench", campaignId, "bias-trend", threshold], enabled: campaignId != null,
    queryFn: () => call<BiasTrend>("GET", `/api/experiments/workbench/${campaignId}/bias-trend?threshold_rmse=${threshold}`) });
  return (
    <ProCard bordered title={t("workbench.bias.title", "预测偏差趋势（预测−实测）")} loading={q.isLoading && campaignId != null}
      extra={<Space>{extra}<span>{t("workbench.bias.threshold_label", "RMSE 阈值")}</span>
        <InputNumber size="small" min={0} value={threshold} onChange={(v) => setThreshold(Number(v ?? 50))} style={{ width: 90 }} /></Space>}>
      <ErrorAlert error={q.error} />
      {campaignId == null ? <Typography.Text type="secondary">{t("workbench.bias.pick", "选择一个实验台账查看偏差趋势")}</Typography.Text>
        : q.data && <BiasTrendView trend={q.data.trend} alerts={q.data.alerts} threshold={threshold} />}
    </ProCard>
  );
}
