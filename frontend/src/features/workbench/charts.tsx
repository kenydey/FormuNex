import { Empty, Space, Table, Tag, Typography, theme } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Chart } from "@/shared/Chart";
import { buildSurface, type SurfacePoint } from "./surface";
import type { BiasTrendPoint } from "./types";

// Sequential ramp readable on both light and dark backgrounds.
const RAMP = ["#313695", "#4575b4", "#74add1", "#abd9e9", "#fee090", "#fdae61", "#f46d43", "#d73027"];
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(1) : v.toPrecision(3));

/** Response surface (legacy ``charts/ContourPlot``): IDW-interpolated heatmap + measured points. */
export function ContourChart({ points, xLabel, yLabel, metric, height = 420 }: { points: SurfacePoint[]; xLabel: string; yLabel: string; metric: string; height?: number }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const s = useMemo(() => buildSurface(points), [points]);
  if (!s) return <Empty description={t("workbench.surface.too_few", "数据点不足（需要 ≥3 个已完成且有测量的实验点）")} />;
  const half = (d: number) => d / 2;
  return (
    <Chart height={height} option={{
      title: { text: t("workbench.surface.title", "响应曲面 · {{m}}", { m: metric }), subtext: `${xLabel} × ${yLabel}`, left: "center", textStyle: { fontSize: 14 } },
      tooltip: { formatter: (p: unknown) => {
        const d = p as { seriesType: string; value: number[] };
        if (d.seriesType === "heatmap") return `${xLabel}: ${fmt(s.gridX[d.value[0]])}<br/>${yLabel}: ${fmt(s.gridY[d.value[1]])}<br/>${metric} ≈ ${fmt(d.value[2])}`;
        return `${xLabel}: ${fmt(d.value[0])}<br/>${yLabel}: ${fmt(d.value[1])}<br/>${metric}: ${fmt(d.value[2])}`;
      } },
      grid: { left: 70, right: 90, top: 70, bottom: 50 },
      xAxis: [
        { type: "category", data: s.gridX.map(fmt), show: false },
        { type: "value", name: xLabel, nameLocation: "middle", nameGap: 30, position: "bottom",
          min: s.gridX[0] - half(s.dx), max: s.gridX[s.gridX.length - 1] + half(s.dx), axisLabel: { formatter: (v: number) => fmt(v) } },
      ],
      yAxis: [
        { type: "category", data: s.gridY.map(fmt), show: false },
        { type: "value", name: yLabel, nameLocation: "middle", nameGap: 50, position: "left",
          min: s.gridY[0] - half(s.dy), max: s.gridY[s.gridY.length - 1] + half(s.dy), axisLabel: { formatter: (v: number) => fmt(v) } },
      ],
      visualMap: { min: s.zMin, max: s.zMax, calculable: true, right: 10, top: "middle", seriesIndex: 0, inRange: { color: RAMP }, text: [fmt(s.zMax), fmt(s.zMin)] },
      series: [
        { type: "heatmap", xAxisIndex: 0, yAxisIndex: 0, data: s.cells, progressive: 0, emphasis: { disabled: true } },
        { type: "scatter", xAxisIndex: 1, yAxisIndex: 1, symbolSize: 10, data: points.map((p) => [p.x, p.y, p.z]),
          itemStyle: { color: token.colorBgContainer, borderColor: token.colorText, borderWidth: 1.5 } },
      ],
    }} />
  );
}

export interface ParallelAxis { key: string; label: string; direction?: "maximize" | "minimize" | "match_target" }
export interface ParallelItem { id: string; values: Record<string, number | null | undefined> }

/**
 * Parallel coordinates (legacy ``charts/ParallelCoordinates``): one line per item, drag on an axis to brush.
 * Minimised axes are inverted so "better" is always up. Highlighted ids (e.g. the Pareto frontier) are drawn bold.
 */
export function ParallelChart({ items, axes, highlight, height = 380 }: { items: ParallelItem[]; axes: ParallelAxis[]; highlight?: Set<string>; height?: number }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  if (axes.length < 2 || !items.length) return <Empty description={t("workbench.parallel.empty", "至少需要 2 个指标和 1 个数据点")} />;
  const line = (it: ParallelItem) => axes.map((a) => it.values[a.key] ?? null);
  const hl = items.filter((i) => highlight?.has(i.id));
  const rest = items.filter((i) => !highlight?.has(i.id));
  const lineData = (arr: ParallelItem[]) => arr.map((i) => ({ name: i.id, value: line(i) }));
  return (
    <>
      <Typography.Text type="secondary">
        {t("workbench.parallel.hint", "{{n}} 条 · 在坐标轴上拖动可刷选，点击轴上空白处清除", { n: items.length })}
      </Typography.Text>
      <Chart height={height}
        option={{
          tooltip: { trigger: "item", formatter: (p: unknown) => {
            const d = p as { name: string; value: (number | null)[] };
            return [d.name, ...axes.map((a, i) => `${a.label}: ${d.value[i] ?? "—"}`)].join("<br/>");
          } },
          legend: highlight?.size ? { data: [t("workbench.parallel.highlight", "前沿"), t("workbench.parallel.others", "其他")], top: 0 } : undefined,
          parallelAxis: axes.map((a, i) => ({ dim: i, name: `${a.label}${a.direction === "minimize" ? " ↓" : a.direction === "maximize" ? " ↑" : ""}`,
            inverse: a.direction === "minimize", scale: true, nameTextStyle: { color: token.colorText } })),
          parallel: { left: 60, right: 80, top: 50, bottom: 30 },
          series: [
            { name: t("workbench.parallel.others", "其他"), type: "parallel", data: lineData(rest), lineStyle: { width: 1, opacity: 0.45, color: token.colorPrimary },
              inactiveOpacity: 0.05, activeOpacity: 1 },
            ...(hl.length ? [{ name: t("workbench.parallel.highlight", "前沿"), type: "parallel" as const, data: lineData(hl),
              lineStyle: { width: 2.5, opacity: 1, color: token.colorSuccess }, inactiveOpacity: 0.05, activeOpacity: 1 }] : []),
          ],
        }} />
    </>
  );
}

/** Prediction-bias RMSE trend over syncs (legacy ``charts/BiasTrendChart`` + ``BiasTrendPanel`` table). */
export function BiasTrendView({ trend, alerts, threshold }: { trend: BiasTrendPoint[]; alerts: string[]; threshold: number }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  if (!trend.length) return <Empty description={t("workbench.bias.empty", "该台账还没有预测偏差记录（同步含实测的行后生成）")} />;
  const metrics = Array.from(new Set(trend.flatMap((p) => Object.keys(p.by_metric))));
  return (
    <>
      <Chart height={260} option={{
        tooltip: { trigger: "axis" },
        legend: { data: metrics.map((m) => `${m} RMSE`) },
        xAxis: { type: "category", data: trend.map((p, i) => `R${i + 1}(${p.n_rows})`) },
        yAxis: { type: "value", name: "RMSE" },
        series: metrics.map((m, i) => ({
          name: `${m} RMSE`, type: "line" as const, smooth: true, data: trend.map((p) => p.by_metric[m]?.rmse ?? null),
          ...(i === 0 ? { markLine: { symbol: "none", silent: true, lineStyle: { type: "dashed" as const, color: token.colorWarning },
            label: { formatter: t("workbench.bias.threshold", "阈值 {{v}}", { v: threshold }) }, data: [{ yAxis: threshold }] } } : {}),
        })),
      }} />
      {alerts.map((a) => <Tag key={a} color="warning" style={{ marginBottom: 4 }}>{a}</Tag>)}
      <Table size="small" rowKey="k" pagination={false} style={{ marginTop: 8 }}
        dataSource={trend.slice(-5).reverse().map((p, i) => ({ ...p, k: `${p.at ?? ""}-${i}` }))}
        columns={[
          { title: t("workbench.bias.at", "时间"), dataIndex: "at", render: (v: string | null) => (v ? new Date(v).toLocaleString() : "—") },
          { title: "n", dataIndex: "n_rows", width: 60 },
          { title: t("workbench.bias.metrics", "指标 · 均值偏差 / RMSE / MAE"), render: (_v, p: BiasTrendPoint) => (
            <Space wrap size={4}>{Object.entries(p.by_metric).map(([m, s]) => (
              <Tag key={m}>{m}: {s.mean_error > 0 ? `+${s.mean_error}` : s.mean_error} / {fmt(s.rmse)} / {fmt(s.mae)}</Tag>))}</Space>
          ) },
        ]} />
    </>
  );
}
