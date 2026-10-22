import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Empty, Input, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";
import type { LabTask } from "@/features/lab/types";
import { ParallelChart } from "@/features/workbench/charts";
import { WinningModal } from "./WinningModal";
import { Scatter3D } from "./Scatter3D";

interface Obj { metric: string; direction: "maximize" | "minimize" | "match_target"; target_value?: number | null }
interface ParetoPoint { id: string; rank: number | null; values: Record<string, number>; hazard_score?: number; signal_word?: string | null; pictograms?: string[] }
interface SensitivityOut {
  surrogate: { r2: number; reliable: boolean; reliability_note?: string };
  local: { at: number[]; gradient: number[]; gradient_per_range: number[] };
  sobol_first_order: { factor: string; index: number }[];
}

/** 帕累托前沿 (方案 §5.4): reviewed lab results; hazard score can be a minimised objective. */
export default function ParetoPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const project = useQuery({ queryKey: ["project", projectId], enabled: !!projectId, queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`) });
  const tasks = useQuery({ queryKey: ["lab", "tasks", "pareto", projectId], queryFn: () => call<LabTask[]>("GET", `/api/lab/tasks${projectId ? `?project_id=${projectId}` : ""}`) });
  const done = (tasks.data ?? []).filter((x) => x.measured && Object.keys(x.measured).length);
  const metrics = Array.from(new Set(done.flatMap((x) => Object.keys(x.measured ?? {}))));
  const projectObjs: Obj[] = (project.data?.workspace.requirement?.objectives ?? []).map((o) => ({ metric: o.metric, direction: o.direction, target_value: o.target_value }));
  const [objs, setObjs] = useState<Obj[] | null>(null);
  // 补齐 2-19: 目标支持到 3 个（后端 pareto 接受 1~6 个目标）。
  const objectives = (objs ?? projectObjs.filter((o) => metrics.includes(o.metric)).slice(0, 2)).slice(0, 3);
  const [hazard, setHazard] = useState(false);
  const [casMap, setCasMap] = useState<Record<string, string>>({});
  const [winning, setWinning] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [onlySelected, setOnlySelected] = useState(false);
  const factorNames = useMemo(() => Array.from(new Set(done.flatMap((x) => Object.entries(x.factors).filter(([, v]) => typeof v === "number").map(([k]) => k)))), [done]);

  const run = useMutation({
    mutationFn: () => {
      const objList = [...objectives, ...(hazard ? [{ metric: "hazard_score", direction: "minimize" as const }] : [])];
      const points = done.filter((x) => objectives.every((o) => x.measured?.[o.metric] != null)).map((x) => ({
        id: x.task_no,
        values: Object.fromEntries(objectives.map((o) => [o.metric, x.measured![o.metric]])),
        composition: hazard ? Object.entries(x.factors).filter(([k, v]) => typeof v === "number" && casMap[k] !== undefined)
          .map(([k, v]) => ({ name: k, pct: v as number, cas: casMap[k] || null })) : undefined,
      }));
      return call<{ points: ParetoPoint[]; frontier: string[] }>("POST", "/api/cockpit/pareto", { objectives: objList, points });
    },
  });
  const byNo = new Map(done.map((x) => [x.task_no, x]));
  const res = run.data;
  const [xm, ym, zm] = [objectives[0]?.metric, objectives[1]?.metric, objectives[2]?.metric];
  const val = (p: ParetoPoint, m?: string) => (m === "hazard_score" ? p.hazard_score : m ? p.values[m] : undefined);
  const frontierPoints = (res?.points ?? []).filter((p) => p.rank === 0);
  const otherPoints = (res?.points ?? []).filter((p) => p.rank !== 0);

  // 补齐 2-19: 刷选联动 — brushSelected 给出各 series 的 dataIndex，映射回点 id。
  const onBrushSelected = (params: unknown) => {
    const batch = (params as { batch?: { selected?: { seriesIndex: number; dataIndex: number[] }[] }[] }).batch?.[0];
    const ids: string[] = [];
    for (const sel of batch?.selected ?? []) {
      const arr = sel.seriesIndex === 0 ? frontierPoints : otherPoints;
      for (const i of sel.dataIndex) if (arr[i]) ids.push(arr[i].id);
    }
    setSelectedIds(ids);
  };

  // 补齐 2-19: 灵敏度详情 — 用全部已复核任务的数值因子 + 首个目标实测值调 /sensitivity。
  const sensitivity = useMutation({
    mutationFn: (pointId: string) => {
      const factorSet = factorNames.filter((f) => done.every((x) => typeof x.factors[f] === "number"));
      const points = done
        .filter((x) => x.measured?.[objectives[0].metric] != null && factorSet.every((f) => typeof x.factors[f] === "number"))
        .map((x) => ({ id: x.task_no, factors: Object.fromEntries(factorSet.map((f) => [f, x.factors[f] as number])), objective: x.measured![objectives[0].metric] }));
      return call<SensitivityOut>("POST", "/api/cockpit/sensitivity", { points, selected_id: pointId });
    },
  });
  const selectedId = selectedIds[0] ?? null;
  const selectedTask = selectedId ? byNo.get(selectedId) : undefined;

  const tableData = [...(res?.points ?? [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
    .filter((p) => !onlySelected || selectedIds.includes(p.id));

  return (
    <PageContainer subTitle={t("pareto.sub", "只使用已复核的实测值；点击前沿上的点可设为定型")}>
      <ProCard bordered>
        <Space wrap>
          <span>{t("pareto.objectives", "目标")}</span>
          <Select mode="multiple" style={{ minWidth: 320 }} maxCount={3} value={objectives.map((o) => `${o.metric}:${o.direction}`)}
            onChange={(vals: string[]) => setObjs(vals.slice(0, 3).map((v) => {
              const [metric, direction] = v.split(":");
              // P1-16: 目标编辑不得丢 target_value——先从当前目标、再从项目契约找回同 metric 的目标值，
              // 否则 match_target 在后端静默退化为 maximize 排序。方向切走再切回也能找回。
              const prev = [...(objs ?? []), ...projectObjs].find((o) => o.metric === metric && o.target_value != null);
              return { metric, direction: direction as Obj["direction"], ...(prev?.target_value != null ? { target_value: prev.target_value } : {}) };
            }))}
            options={metrics.flatMap((m) => [{ value: `${m}:maximize`, label: `${m} ↑` }, { value: `${m}:minimize`, label: `${m} ↓` }, { value: `${m}:match_target`, label: `${m} 目标值` }])} />
          <span>{t("pareto.objectives_hint", "最多 3 个目标；3 个目标时可用 3D 散点")}</span>
          <Switch checked={hazard} onChange={setHazard} /> {t("pareto.hazard", "危害评分作为最小化目标（由组成计算）")}
          <Button type="primary" disabled={!objectives.length} loading={run.isPending} onClick={() => run.mutate()}>{t("pareto.run", "计算前沿")}</Button>
        </Space>
        {hazard && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">{t("pareto.cas_map", "把数值因子映射为组分（填 CAS；留空按名称）：")}</Typography.Text>
            <Space wrap style={{ marginTop: 8 }}>
              {factorNames.map((f) => <Input key={f} addonBefore={f} style={{ width: 240 }} placeholder="CAS" value={casMap[f] ?? ""}
                onChange={(e) => setCasMap({ ...casMap, [f]: e.target.value })} />)}
            </Space>
          </div>
        )}
      </ProCard>
      <ErrorAlert error={run.error ?? tasks.error} />
      {!done.length && <Empty style={{ marginTop: 24 }} description={t("pareto.empty", "当前项目还没有已复核的实验结果")} />}
      {res && xm && (
        <ProCard bordered style={{ marginTop: 16 }}>
          <Tabs items={[
            { key: "scatter", label: t("pareto.view_scatter", "散点"), children: ym ? (
              <Chart height={420}
                option={{
                  tooltip: { trigger: "item", formatter: (p: unknown) => { const d = (p as { data: { name: string; value: number[] } }).data; return `${d.name}<br/>${xm}: ${d.value[0]}<br/>${ym}: ${d.value[1]}`; } },
                  xAxis: { name: xm, scale: true }, yAxis: { name: ym, scale: true },
                  legend: { data: [t("pareto.frontier", "帕累托前沿"), t("pareto.others", "其他")] },
                  brush: { toolbox: ["rect", "clear"], xAxisIndex: 0, yAxisIndex: 0 },
                  series: [
                    { name: t("pareto.frontier", "帕累托前沿"), type: "scatter", symbolSize: 14,
                      data: frontierPoints.map((p) => ({ name: p.id, value: [val(p, xm), val(p, ym)] })) },
                    { name: t("pareto.others", "其他"), type: "scatter", symbolSize: 8, itemStyle: { opacity: 0.5 },
                      data: otherPoints.map((p) => ({ name: p.id, value: [val(p, xm), val(p, ym)] })) },
                  ],
                }}
                onEvents={{ click: (p) => { const id = (p as { name: string }).name; if (id) setSelectedIds([id]);
                  // P1-17: 散点点击复用表格的 rank === 0 门禁——选中任意点仍可看详情，
                  // 但只有前沿点（rank 0）才能打开“设为定型”弹窗。
                  const point = (res?.points ?? []).find((pp) => pp.id === id);
                  const exp = byNo.get(id)?.experiment_id; if (exp && point?.rank === 0 && can("formulation.freeze")) setWinning(exp); }, brushSelected: onBrushSelected }}
              />
            ) : (
              <Empty style={{ padding: 24 }} description={t("pareto.scatter_needs_two", "散点图需要两个目标；当前为单目标，请看平行坐标与下方点表")} />
            ) },
            ...(zm ? [{ key: "scatter3d", label: t("pareto.view_3d", "3D 散点"), children: (
              <Scatter3D
                points={res.points.filter((p) => val(p, xm) != null && val(p, ym) != null && val(p, zm) != null)
                  .map((p) => ({ id: p.id, values: [val(p, xm)!, val(p, ym)!, val(p, zm)!] as [number, number, number], frontier: p.rank === 0 }))}
                axisNames={[xm, ym, zm]}
                selectedId={selectedId}
                onSelect={(id) => setSelectedIds([id])}
              />
            ) }] : []),
            { key: "parallel", label: t("pareto.view_parallel", "平行坐标"), children: (
              <ParallelChart
                axes={[...objectives.map((o) => ({ key: o.metric, label: o.metric, direction: o.direction })),
                  ...(hazard ? [{ key: "hazard_score", label: t("pareto.hazard_col", "危害评分"), direction: "minimize" as const }] : [])]}
                items={res.points.map((p) => ({ id: p.id, values: { ...p.values, ...(hazard ? { hazard_score: p.hazard_score } : {}) } }))}
                highlight={new Set(res.points.filter((p) => p.rank === 0).map((p) => p.id))} />
            ) },
          ]} />
          <Space wrap style={{ marginTop: 8 }}>
            <span>{t("pareto.selected", "已选 {{n}} 点", { n: selectedIds.length })}</span>
            <Switch checked={onlySelected} onChange={setOnlySelected} /> {t("pareto.only_selected", "表格仅看选中")}
            <Button size="small" onClick={() => { setSelectedIds([]); setOnlySelected(false); }}>{t("pareto.clear_selection", "清除选中")}</Button>
          </Space>
          <Table size="small" rowKey="id" pagination={false} dataSource={tableData} rowClassName={(p) => (selectedIds.includes(p.id) ? "ant-table-row-selected" : "")} columns={[
            { title: t("tasks.no", "任务单"), dataIndex: "id" },
            { title: t("pareto.rank", "前沿层级"), dataIndex: "rank", render: (r: number) => (r === 0 ? <Tag color="green">0</Tag> : r) },
            ...objectives.map((o) => ({ title: o.metric, render: (_v: unknown, p: ParetoPoint) => p.values[o.metric] })),
            ...(hazard ? [{ title: t("pareto.hazard_col", "危害评分"), dataIndex: "hazard_score" }] : []),
            { title: "", render: (_v: unknown, p: ParetoPoint) => <Button size="small" onClick={() => setSelectedIds([p.id])}>{t("pareto.detail", "详情")}</Button> },
            { title: "", render: (_v: unknown, p: ParetoPoint) => can("formulation.freeze") && p.rank === 0 &&
              <Button size="small" onClick={() => setWinning(byNo.get(p.id)?.experiment_id ?? null)}>{t("pareto.win", "设为定型")}</Button> },
          ]} />
          {/* 补齐 2-19: 刷选/点击联动详情 + 灵敏度 */}
          {selectedTask && (
            <ProCard bordered style={{ marginTop: 16 }} title={t("pareto.detail_title", "选中点 {{id}}", { id: selectedTask.task_no })}>
              <Space direction="vertical" style={{ width: "100%" }}>
                <span>{t("pareto.detail_factors", "因子：{{f}}", { f: JSON.stringify(selectedTask.factors) })}</span>
                <span>{t("pareto.detail_measured", "实测：{{m}}", { m: JSON.stringify(selectedTask.measured) })}</span>
                <Button type="primary" disabled={!objectives[0]} loading={sensitivity.isPending}
                  onClick={() => sensitivity.mutate(selectedTask.task_no)}>{t("pareto.sensitivity", "灵敏度分析（局部梯度 + Sobol）")}</Button>
                <ErrorAlert error={sensitivity.error} />
                {sensitivity.data && (
                  <>
                    {!sensitivity.data.surrogate.reliable && <Alert type="warning" showIcon message={sensitivity.data.surrogate.reliability_note ?? t("pareto.surrogate_unreliable", "代理模型 R² 偏低，灵敏度仅供参考")} />}
                    <Table size="small" rowKey="factor" pagination={false} dataSource={sensitivity.data.sobol_first_order} columns={[
                      { title: t("pareto.factor", "因子"), dataIndex: "factor" },
                      { title: t("pareto.sobol", "一阶 Sobol 指数"), dataIndex: "index", render: (v: number) => v.toFixed(3) },
                      { title: t("pareto.gradient", "局部梯度（每单位因子）"), render: (_v: unknown, r: { factor: string }) => { const i = sensitivity.data!.sobol_first_order.findIndex((s) => s.factor === r.factor); return sensitivity.data!.local.gradient[i]?.toFixed(4); } },
                      { title: t("pareto.gradient_range", "局部梯度（每全距）"), render: (_v: unknown, r: { factor: string }) => { const i = sensitivity.data!.sobol_first_order.findIndex((s) => s.factor === r.factor); return sensitivity.data!.local.gradient_per_range[i]?.toFixed(4); } },
                    ]} />
                  </>
                )}
              </Space>
            </ProCard>
          )}
          <Alert style={{ marginTop: 8 }} type="info" showIcon message={t("pareto.hazard_note", "危害评分仅用于排序（按危害类别与严重度加权），不是法规量。")} />
        </ProCard>
      )}
      <WinningModal experimentId={winning} open={winning != null} onClose={() => setWinning(null)} />
    </PageContainer>
  );
}
