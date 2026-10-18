import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Input, Space, Table, Tag } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";

interface AcqPoint {
  iteration: number;
  acquisition: number | null;
  acquisition_name: string;
  best_score: number;
  engine: string;
}

interface TaskOut {
  result?: { acquisition_history?: AcqPoint[]; engine?: string };
}

/** 补齐 2-16: 采集函数趋势 — 采集值 + 最佳得分双曲线 + 趋势摘要。 */
export default function AcquisitionTrendPage() {
  const { t } = useTranslation();
  const [taskId, setTaskId] = useState("");
  const fetch = useMutation({ mutationFn: () => call<TaskOut>("GET", `/api/tasks/${taskId}`) });
  const history = fetch.data?.result?.acquisition_history ?? [];
  const nonNull = history.filter((p) => p.acquisition != null);
  // 趋势：后半均值 vs 前半均值（与后端 AcquisitionHistory.summary 同口径，±5% 为 flat）。
  let trend = "—";
  if (nonNull.length >= 4) {
    const half = Math.floor(nonNull.length / 2);
    const first = nonNull.slice(0, half).reduce((a, p) => a + (p.acquisition ?? 0), 0) / half;
    const second = nonNull.slice(half).reduce((a, p) => a + (p.acquisition ?? 0), 0) / (nonNull.length - half);
    trend = second < first * 0.95 ? t("acq.trend_declining", "下降（收敛中）") : second > first * 1.05 ? t("acq.trend_rising", "上升（仍在探索）") : t("acq.trend_flat", "平稳");
  } else if (history.length) {
    trend = t("acq.trend_insufficient", "数据不足（需 ≥4 个非空采集值）");
  }
  const engine = history[0]?.engine ?? fetch.data?.result?.engine ?? "—";
  const acqName = history.find((p) => p.acquisition_name)?.acquisition_name ?? "—";

  return (
    <PageContainer subTitle={t("acq.sub", "按迭代查看采集函数值与最佳得分；采集值下降通常意味着寻优收敛")}>
      <ProCard bordered>
        <Space wrap>
          <Input style={{ width: 320 }} placeholder={t("acq.task_id", "寻优任务 id（task_id）")} value={taskId} onChange={(e) => setTaskId(e.target.value)} />
          <Button type="primary" disabled={!taskId.trim()} loading={fetch.isPending} onClick={() => fetch.mutate()}>{t("acq.load", "加载")}</Button>
        </Space>
      </ProCard>
      <ErrorAlert error={fetch.error} />
      {fetch.data && (
        <>
          {history.length === 0 && (
            <Alert style={{ marginTop: 16 }} type="info" showIcon
              message={t("acq.empty", "该任务无采集函数历史：BayBE / TPE / LogEI 引擎不报标量采集函数（属预期，非数据缺失）")} />
          )}
          {history.length > 0 && (
            <>
              <ProCard bordered style={{ marginTop: 16 }}>
                <Space wrap>
                  <span>{t("acq.engine", "引擎：{{e}}", { e: engine })}</span>
                  <span>{t("acq.name", "采集函数：{{n}}", { n: acqName })}</span>
                  <Tag>{trend}</Tag>
                </Space>
                <Chart height={380} option={{
                  tooltip: { trigger: "axis" },
                  legend: { data: [t("acq.acquisition", "采集值"), t("acq.best", "最佳得分")] },
                  xAxis: { type: "category", name: t("acq.iteration", "迭代"), data: history.map((p) => p.iteration) },
                  yAxis: [{ type: "value", name: t("acq.acquisition", "采集值") }, { type: "value", name: t("acq.best", "最佳得分") }],
                  series: [
                    { name: t("acq.acquisition", "采集值"), type: "line", data: history.map((p) => p.acquisition) },
                    { name: t("acq.best", "最佳得分"), type: "line", yAxisIndex: 1, lineStyle: { type: "dashed" }, data: history.map((p) => p.best_score) },
                  ],
                }} />
              </ProCard>
              <ProCard bordered style={{ marginTop: 16 }}>
                <Table size="small" rowKey="iteration" pagination={false} dataSource={history} columns={[
                  { title: t("acq.iteration", "迭代"), dataIndex: "iteration" },
                  { title: t("acq.acquisition", "采集值"), dataIndex: "acquisition", render: (v: number | null) => (v == null ? "—" : v.toFixed(4)) },
                  { title: t("acq.name", "采集函数"), dataIndex: "acquisition_name" },
                  { title: t("acq.best", "最佳得分"), dataIndex: "best_score", render: (v: number) => v.toFixed(4) },
                  { title: t("acq.engine", "引擎"), dataIndex: "engine" },
                ]} />
              </ProCard>
            </>
          )}
        </>
      )}
    </PageContainer>
  );
}
