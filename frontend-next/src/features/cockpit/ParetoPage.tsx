import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Empty, Input, Select, Space, Switch, Table, Tag, Typography } from "antd";
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
import { WinningModal } from "./WinningModal";

interface Obj { metric: string; direction: "maximize" | "minimize" | "match_target"; target_value?: number | null }
interface ParetoPoint { id: string; rank: number | null; values: Record<string, number>; hazard_score?: number; signal_word?: string | null; pictograms?: string[] }

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
  const objectives = objs ?? projectObjs.filter((o) => metrics.includes(o.metric)).slice(0, 2);
  const [hazard, setHazard] = useState(false);
  const [casMap, setCasMap] = useState<Record<string, string>>({});
  const [winning, setWinning] = useState<number | null>(null);
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
  const [xm, ym] = [objectives[0]?.metric, objectives[1]?.metric ?? (hazard ? "hazard_score" : undefined)];
  const val = (p: ParetoPoint, m?: string) => (m === "hazard_score" ? p.hazard_score : m ? p.values[m] : undefined);

  return (
    <PageContainer subTitle={t("pareto.sub", "只使用已复核的实测值；点击前沿上的点可设为定型")}>
      <ProCard bordered>
        <Space wrap>
          <span>{t("pareto.objectives", "目标")}</span>
          <Select mode="multiple" style={{ minWidth: 320 }} value={objectives.map((o) => `${o.metric}:${o.direction}`)}
            onChange={(vals: string[]) => setObjs(vals.map((v) => { const [metric, direction] = v.split(":"); return { metric, direction: direction as Obj["direction"] }; }))}
            options={metrics.flatMap((m) => [{ value: `${m}:maximize`, label: `${m} ↑` }, { value: `${m}:minimize`, label: `${m} ↓` }])} />
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
      {res && xm && ym && (
        <ProCard bordered style={{ marginTop: 16 }}>
          <Chart height={420}
            option={{
              tooltip: { trigger: "item", formatter: (p: unknown) => { const d = (p as { data: { name: string; value: number[] } }).data; return `${d.name}<br/>${xm}: ${d.value[0]}<br/>${ym}: ${d.value[1]}`; } },
              xAxis: { name: xm, scale: true }, yAxis: { name: ym, scale: true },
              legend: { data: [t("pareto.frontier", "帕累托前沿"), t("pareto.others", "其他")] },
              series: [
                { name: t("pareto.frontier", "帕累托前沿"), type: "scatter", symbolSize: 14,
                  data: res.points.filter((p) => p.rank === 0).map((p) => ({ name: p.id, value: [val(p, xm), val(p, ym)] })) },
                { name: t("pareto.others", "其他"), type: "scatter", symbolSize: 8, itemStyle: { opacity: 0.5 },
                  data: res.points.filter((p) => p.rank !== 0).map((p) => ({ name: p.id, value: [val(p, xm), val(p, ym)] })) },
              ],
            }}
            onEvents={{ click: (p) => { const exp = byNo.get((p as { name: string }).name)?.experiment_id; if (exp && can("formulation.freeze")) setWinning(exp); } }}
          />
          <Table size="small" rowKey="id" pagination={false} dataSource={[...res.points].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))} columns={[
            { title: t("tasks.no", "任务单"), dataIndex: "id" },
            { title: t("pareto.rank", "前沿层级"), dataIndex: "rank", render: (r: number) => (r === 0 ? <Tag color="green">0</Tag> : r) },
            ...objectives.map((o) => ({ title: o.metric, render: (_v: unknown, p: ParetoPoint) => p.values[o.metric] })),
            ...(hazard ? [{ title: t("pareto.hazard_col", "危害评分"), dataIndex: "hazard_score" }] : []),
            { title: "", render: (_v: unknown, p: ParetoPoint) => can("formulation.freeze") && p.rank === 0 &&
              <Button size="small" onClick={() => setWinning(byNo.get(p.id)?.experiment_id ?? null)}>{t("pareto.win", "设为定型")}</Button> },
          ]} />
          <Alert style={{ marginTop: 8 }} type="info" showIcon message={t("pareto.hazard_note", "危害评分仅用于排序（按危害类别与严重度加权），不是法规量。")} />
        </ProCard>
      )}
      <WinningModal experimentId={winning} open={winning != null} onClose={() => setWinning(null)} />
    </PageContainer>
  );
}
