import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { Empty, Skeleton, Table } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import type { OrgDashboardStats } from "./types";

/** 组织级 R&D 仪表盘 (GET /api/org/dashboard). */
export default function OrgDashboardPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["settings", "org-dashboard"], queryFn: () => call<OrgDashboardStats>("GET", "/api/org/dashboard") });
  const d = q.data;
  const domains = Object.entries(d?.by_domain ?? {});
  return (
    <PageContainer title={t("settings.org.title", "组织看板")}>
      <ErrorAlert error={q.error} />
      {q.isLoading && <Skeleton active />}
      {d && (
        <ProCard ghost gutter={[16, 16]} wrap>
          <ProCard colSpan={24} ghost>
            <StatisticCard.Group direction="row" style={{ flexWrap: "wrap" }}>
              <StatisticCard statistic={{ title: t("settings.org.experiments", "实验总数"), value: d.total_experiments }} />
              <StatisticCard statistic={{ title: t("settings.org.campaigns", "Campaign 数"), value: d.total_campaigns }} />
              <StatisticCard statistic={{ title: t("settings.org.projects", "项目总数"), value: d.total_projects }} />
              <StatisticCard statistic={{ title: t("settings.org.active", "活跃项目"), value: d.active_projects }} />
              <StatisticCard statistic={{ title: t("settings.org.convergence", "收敛率"), value: Math.round(d.convergence_rate * 100), suffix: "%" }} />
              <StatisticCard statistic={{ title: t("settings.org.rounds", "平均收敛轮次"), value: d.avg_rounds_to_converge }} />
              <StatisticCard statistic={{ title: t("settings.org.recent", "近 7 天新增实验 / Campaign"),
                value: d.recent_activity.experiments_added, suffix: `/ ${d.recent_activity.campaigns_created}` }} />
            </StatisticCard.Group>
          </ProCard>
          <ProCard colSpan={{ xs: 24, lg: 10 }} bordered title={t("settings.org.domains", "领域分布")}>
            {domains.length === 0 ? <Empty /> : (
              <Chart height={300} option={{
                tooltip: { trigger: "item" },
                series: [{ type: "pie", radius: ["40%", "70%"], data: domains.map(([name, value]) => ({ name, value })) }],
              }} />
            )}
          </ProCard>
          <ProCard colSpan={{ xs: 24, lg: 14 }} bordered title={t("settings.org.ingredients", "常用原料 Top 20")}>
            {d.ingredient_frequency.length === 0 ? <Empty /> : (
              <Chart height={Math.max(240, d.ingredient_frequency.length * 22)} option={{
                tooltip: { trigger: "axis" },
                grid: { left: 140, right: 24, top: 8, bottom: 24 },
                xAxis: { type: "value" },
                yAxis: { type: "category", inverse: true, data: d.ingredient_frequency.map((i) => i.ingredient_name) },
                series: [{ type: "bar", name: t("settings.org.exp_count", "实验次数"), data: d.ingredient_frequency.map((i) => i.experiment_count) }],
              }} />
            )}
          </ProCard>
          <ProCard colSpan={24} bordered title={t("settings.org.top", "最佳记录")}>
            <Table size="small" pagination={false} rowKey={(r) => `${r.experiment_id}-${r.metric}`} dataSource={d.top_performers}
              columns={[
                { title: t("settings.org.metric", "指标"), dataIndex: "metric" },
                { title: t("settings.org.value", "数值"), dataIndex: "value" },
                { title: t("settings.org.project", "项目"), dataIndex: "project_title" },
                { title: t("settings.org.formulation", "配方"), dataIndex: "formulation_preview", ellipsis: true },
                { title: t("settings.org.measured_at", "测量时间"), dataIndex: "measured_at", width: 180 },
              ]} />
          </ProCard>
          <ProCard colSpan={24} bordered title={t("settings.org.ingredient_table", "原料使用明细")}>
            <Table size="small" pagination={false} rowKey="ingredient_name" dataSource={d.ingredient_frequency}
              columns={[
                { title: t("settings.org.ingredient", "原料"), dataIndex: "ingredient_name" },
                { title: t("settings.org.exp_count", "实验次数"), dataIndex: "experiment_count", width: 120 },
                { title: t("settings.org.avg_pct", "平均用量 %"), dataIndex: "avg_weight_pct", width: 120 },
                { title: t("settings.org.best_metric", "最佳指标"), dataIndex: "best_result_metric", render: (v: string | null) => v ?? "—" },
              ]} />
          </ProCard>
        </ProCard>
      )}
    </PageContainer>
  );
}
