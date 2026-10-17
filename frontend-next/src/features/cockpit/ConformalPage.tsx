import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { Alert, Button, Input, Select, Slider, Space, Switch, Tabs } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import { useCampaigns } from "@/features/workbench/hooks";
import { BiasTrendCard } from "@/features/workbench/panels";

interface HistoryPoint { index: number; id: string | null; predicted: number; actual: number; lower: number | null; upper: number | null; covered: boolean; rolling_coverage: number }
interface Out {
  calibration: { q: number | null; n: number; alpha: number; mean_abs_residual: number; bias: number; finite: boolean; min_points_for_finite_interval: number };
  monitor: { target: number; evaluated: number; coverage: number | null; rolling_coverage: number | null; alarm: boolean; message: string; history: HistoryPoint[] };
  source?: { pairs: number };
}

/** 预测区间与偏差 (方案 §5.4): split conformal intervals and coverage monitoring. */
export default function ConformalPage() {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  const [metric, setMetric] = useState("");
  const [planId, setPlanId] = useState<string | undefined>();
  const [alpha, setAlpha] = useState(0.1);
  const [normalized, setNormalized] = useState(false);
  const [csv, setCsv] = useState("");
  const plans = useQuery({ queryKey: ["doe", "history", projectId], queryFn: () => call<{ items: { plan_id: string; design: string; created_at: string }[] }>("GET", `/api/doe/history?page_size=50${projectId ? `&project_id=${projectId}` : ""}`) });
  const lab = useMutation({ mutationFn: () => call<Out>("POST", "/api/cockpit/conformal/from-lab", { metric, doe_plan_id: planId, project_id: planId ? null : projectId, alpha, normalized }) });
  const manual = useMutation({ mutationFn: () => {
    const pairs = csv.split(/\n/).map((l) => l.split(/[,\t;]/).map((x) => x.trim())).filter((r) => r.length >= 2 && r[0] !== "" && !isNaN(Number(r[0])))
      .map((r) => ({ predicted: Number(r[0]), actual: Number(r[1]), sigma: r[2] ? Number(r[2]) : null }));
    return call<Out>("POST", "/api/cockpit/conformal", { metric, pairs, alpha, normalized });
  } });
  const campaigns = useCampaigns();
  const [biasCampaign, setBiasCampaign] = useState<number | undefined>();
  const biasId = biasCampaign ?? campaigns.items[0]?.id;
  const out = lab.data ?? manual.data;
  const h = out?.monitor.history ?? [];
  return (
    <PageContainer subTitle={t("conformal.sub", "用历史“预测-复核实测”配对校准区间；覆盖率显著低于目标时提示模型漂移")}>
      <ProCard bordered>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input style={{ width: 200 }} placeholder={t("conformal.metric", "指标，如 salt_spray_hours")} value={metric} onChange={(e) => setMetric(e.target.value)} />
          <span>{t("conformal.alpha", "置信水平 {{p}}%", { p: Math.round((1 - alpha) * 100) })}</span>
          <Slider style={{ width: 160 }} min={0.05} max={0.3} step={0.05} value={alpha} onChange={setAlpha} />
          <Switch checked={normalized} onChange={setNormalized} /> {t("conformal.normalized", "按 σ 归一化")}
        </Space>
        <Tabs items={[
          { key: "lab", label: t("conformal.from_lab", "从实验任务取数"), children: (
            <Space wrap>
              <Select allowClear style={{ width: 320 }} placeholder={t("conformal.plan", "DOE 计划（不选则用当前项目）")} value={planId} onChange={setPlanId}
                options={(plans.data?.items ?? []).map((p) => ({ value: p.plan_id, label: `${p.design} · ${p.created_at?.slice(0, 10)}` }))} />
              <Button type="primary" disabled={!metric} loading={lab.isPending} onClick={() => { manual.reset(); lab.mutate(); }}>{t("conformal.run", "校准")}</Button>
            </Space>
          ) },
          { key: "manual", label: t("conformal.manual", "粘贴配对数据"), children: (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Input.TextArea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={t("conformal.csv", "每行：预测值, 实测值[, σ]（按时间顺序）")} />
              <Button type="primary" disabled={!csv.trim()} loading={manual.isPending} onClick={() => { lab.reset(); manual.mutate(); }}>{t("conformal.run", "校准")}</Button>
            </Space>
          ) },
        ]} />
      </ProCard>
      <ErrorAlert error={lab.error ?? manual.error} />
      {out && (
        <>
          {out.monitor.alarm && <Alert style={{ marginTop: 16 }} type="error" showIcon message={out.monitor.message} />}
          {!out.calibration.finite && <Alert style={{ marginTop: 16 }} type="warning" showIcon
            message={t("conformal.too_few", "配对数不足：该置信水平至少需要 {{n}} 组才能给出有限区间", { n: out.calibration.min_points_for_finite_interval })} />}
          <ProCard ghost gutter={16} style={{ marginTop: 16 }}>
            <StatisticCard statistic={{ title: t("conformal.q", "区间半宽 q"), value: out.calibration.q ?? "∞", precision: 3 }} />
            <StatisticCard statistic={{ title: t("conformal.coverage", "实际覆盖率"), value: out.monitor.coverage != null ? out.monitor.coverage * 100 : "—", precision: 1, suffix: "%" }} />
            <StatisticCard statistic={{ title: t("conformal.target", "目标覆盖率"), value: out.monitor.target * 100, suffix: "%" }} />
            <StatisticCard statistic={{ title: t("conformal.bias", "平均偏差（预测−实测）"), value: out.calibration.bias, precision: 3 }} />
          </ProCard>
          <ProCard bordered style={{ marginTop: 16 }}>
            <Chart height={380} option={{
              tooltip: { trigger: "axis" },
              legend: { data: [t("conformal.actual", "实测"), t("conformal.pred", "预测"), t("conformal.cov", "滚动覆盖率")] },
              xAxis: { type: "category", data: h.map((p) => p.id ?? String(p.index)) },
              yAxis: [{ type: "value", scale: true }, { type: "value", min: 0, max: 1, name: t("conformal.cov", "滚动覆盖率") }],
              series: [
                { name: "lower", type: "line", data: h.map((p) => p.lower), lineStyle: { opacity: 0 }, stack: "band", symbol: "none" },
                { name: "band", type: "line", data: h.map((p) => (p.upper != null && p.lower != null ? p.upper - p.lower : null)), lineStyle: { opacity: 0 },
                  areaStyle: { opacity: 0.15 }, stack: "band", symbol: "none" },
                { name: t("conformal.pred", "预测"), type: "line", data: h.map((p) => p.predicted) },
                { name: t("conformal.actual", "实测"), type: "scatter", data: h.map((p) => ({ value: p.actual, itemStyle: { color: p.covered ? "#389e0d" : "#cf1322" } })) },
                { name: t("conformal.cov", "滚动覆盖率"), type: "line", yAxisIndex: 1, data: h.map((p) => p.rolling_coverage), lineStyle: { type: "dashed" } },
              ],
            }} />
          </ProCard>
        </>
      )}
      <div style={{ marginTop: 16 }}>
        <BiasTrendCard campaignId={biasId} extra={
          <Select size="small" style={{ width: 240 }} placeholder={t("workbench.campaign", "实验台账")} value={biasId}
            onChange={setBiasCampaign} options={campaigns.items.map((c) => ({ value: c.id, label: `#${c.id} · ${c.name}` }))} />
        } />
      </div>
    </PageContainer>
  );
}
