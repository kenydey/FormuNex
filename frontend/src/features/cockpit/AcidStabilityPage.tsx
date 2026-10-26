import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Button, InputNumber, Space, Table, Tag } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import { FormulationEditor, type DraftFormulation } from "./FormulationEditor";

interface TrendPoint {
  ph: number;
  status: string;
  stable: boolean;
  reasons: string[];
}
interface TrendOut {
  ph_values: number[];
  series: { name: string; points: TrendPoint[] }[];
}

const STATUS_NUM: Record<string, number> = { pass: 0, warn: 1, infeasible: 2 };

/** 补齐 2-21: 酸稳定性趋势 — 组分编辑器 + pH 扫掠曲线 + 每配方结果表。
 * ``forms``/``onFormsChange`` 仅供配方物性 hub 使用：hub 把编辑器状态提升到页级，两 tab 共用。 */
export default function AcidStabilityPage({ forms: controlledForms, onFormsChange }: { forms?: DraftFormulation[]; onFormsChange?: (v: DraftFormulation[]) => void } = {}) {
  const { t } = useTranslation();
  const { can } = useSession();
  const [localForms, setLocalForms] = useState<DraftFormulation[]>([
    { name: "配方 1", ingredients: [{ name: "Zinc dust", weight_pct: 10, role: "pigment" }] },
  ]);
  const forms = controlledForms ?? localForms;
  const setForms = onFormsChange ?? setLocalForms;
  const [phMin, setPhMin] = useState(2);
  const [phMax, setPhMax] = useState(6);
  const [phStep, setPhStep] = useState(0.5);

  const run = useMutation({
    mutationFn: () => call<TrendOut>("POST", "/api/cockpit/acid-stability/trend", {
      formulations: forms, ph_min: phMin, ph_max: phMax, ph_step: phStep,
    }),
  });
  const out = run.data;

  return (
    <PageContainer subTitle={t("acid.sub", "逐配方扫浴液 pH：pass / warn / infeasible，随 pH 变化的趋势与违规原因")}>
      <ProCard bordered>
        <Space direction="vertical" style={{ width: "100%" }}>
          <FormulationEditor formulations={forms} onChange={setForms} />
          <Space wrap>
            <span>pH min</span>
            <InputNumber min={0} max={14} step={0.5} value={phMin} onChange={(v) => setPhMin(v ?? 2)} />
            <span>pH max</span>
            <InputNumber min={0} max={14} step={0.5} value={phMax} onChange={(v) => setPhMax(v ?? 6)} />
            <span>pH step</span>
            <InputNumber min={0.1} max={2} step={0.1} value={phStep} onChange={(v) => setPhStep(v ?? 0.5)} />
            <Button type="primary" disabled={!can("formulation.read")} loading={run.isPending} onClick={() => run.mutate()}>{t("acid.run", "扫掠")}</Button>
          </Space>
        </Space>
      </ProCard>
      <ErrorAlert error={run.error} />
      {out && (
        <>
          <ProCard bordered style={{ marginTop: 16 }}>
            <Chart height={360} option={{
              tooltip: { trigger: "item", formatter: (p: unknown) => { const d = (p as { seriesName: string; data: [number, number] }).data; const st = ["pass", "warn", "infeasible"][d[1]]; return `${(p as { seriesName: string }).seriesName}<br/>pH ${d[0]}: ${st}`; } },
              legend: { data: out.series.map((s) => s.name) },
              xAxis: { type: "value", name: "pH", min: phMin, max: phMax },
              yAxis: { type: "value", name: t("acid.status", "状态"), min: -0.2, max: 2.2, interval: 1, axisLabel: { formatter: (v: number) => ["pass", "warn", "infeasible"][v] ?? v } },
              series: out.series.map((s) => ({
                name: s.name, type: "line", step: "middle",
                data: s.points.map((p) => [p.ph, STATUS_NUM[p.status] ?? 0] as [number, number]),
              })),
            }} />
          </ProCard>
          {out.series.map((s) => (
            <ProCard key={s.name} bordered style={{ marginTop: 16 }} title={s.name}>
              <Table size="small" rowKey="ph" pagination={false} dataSource={s.points} columns={[
                { title: "pH", dataIndex: "ph" },
                { title: t("acid.status", "状态"), dataIndex: "status", render: (v: string) => <Tag color={v === "pass" ? "green" : v === "warn" ? "orange" : "red"}>{v}</Tag> },
                { title: t("acid.reasons", "原因"), dataIndex: "reasons", render: (v: string[]) => v.join("；") },
              ]} />
            </ProCard>
          ))}
        </>
      )}
    </PageContainer>
  );
}
