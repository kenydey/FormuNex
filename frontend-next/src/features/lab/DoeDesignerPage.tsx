import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Form, InputNumber, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";
import { DispatchButton } from "./DispatchButton";
import "./grid.css";

type Run = Record<string, unknown> & { _run?: number; _kind?: string; _compliance_ok?: boolean };
interface LevelFactor { name: string; levels: string }
interface Component { name: string; low: number; high: number; cas?: string }
interface Process { name: string; low: number; high: number; levels?: number; unit?: string }
interface Categorical { name: string; levels: string }
interface ScreenRun { run: number; ok: boolean; reasons: string[]; hazard_score: number; signal_word: string | null; pictograms: string[] }

const parseLevels = (s: string) => s.split(/[,，\s]+/).filter(Boolean).map((x) => (isNaN(Number(x)) ? x : Number(x)));

function RunsTable({ runs, screen }: { runs: Run[]; screen?: ScreenRun[] }) {
  const { t } = useTranslation();
  if (!runs.length) return null;
  const byRun = new Map((screen ?? []).map((s) => [s.run, s]));
  const keys = Object.keys(runs[0]).filter((k) => !k.startsWith("_"));
  return (
    <Table size="small" rowKey="_key" dataSource={runs.map((r, i) => ({ ...r, _key: String(r._run ?? i) }))} pagination={false} scroll={{ x: true }}
      rowClassName={(r) => (byRun.get(r._run as number)?.ok === false ? "fx-row-bad" : "")}
      columns={[
        { title: "#", render: (_v, r, i) => r._run ?? i + 1, width: 50 },
        ...keys.map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => (typeof v === "number" ? Number(v.toFixed(4)) : String(v)) })),
        ...(runs[0]._kind ? [{ title: t("doe.kind", "点类型"), dataIndex: "_kind", render: (v: string) => <Tag>{v}</Tag> }] : []),
        ...(screen ? [{ title: t("doe.compliance", "合规"), render: (_v: unknown, r: Run) => {
          const s = byRun.get(r._run as number);
          return s ? (s.ok ? <Tag color="green">OK · {s.hazard_score}</Tag> : <Tag color="red" title={s.reasons.join("；")}>{s.reasons[0]}</Tag>) : null;
        } }] : []),
      ]}
    />
  );
}

/** DOE 设计 (方案 §5.3, M6): orthogonal arrays, constrained mixtures, D-optimal; compliance pre-screen; dispatch. */
export default function DoeDesignerPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const projectId = useUi((s) => s.projectId);
  const project = useQuery({ queryKey: ["project", projectId], enabled: !!projectId, queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`) });
  const spec = project.data?.workspace.spec_contract;
  const arrays = useQuery({ queryKey: ["doe", "arrays"], queryFn: () => call<{ name: string; runs: number; columns: number }[]>("GET", "/api/doe/designs/orthogonal-arrays") });

  const [oaFactors, setOaFactors] = useState<LevelFactor[]>([{ name: "固化温度", levels: "120, 150, 180" }, { name: "固化时间", levels: "20, 30, 40" }]);
  const [oaInter, setOaInter] = useState<string[]>([]);
  const [oaArray, setOaArray] = useState<string | undefined>();
  const [comps, setComps] = useState<Component[]>([{ name: "树脂", low: 40, high: 60 }, { name: "固化剂", low: 10, high: 25 }, { name: "溶剂", low: 15, high: 40 }]);
  const [proc, setProc] = useState<Process[]>([]);
  const [cats, setCats] = useState<Categorical[]>([]);
  const [mix, setMix] = useState({ order: "quadratic", n: undefined as number | undefined, screen: true, drop: false, cross: "full" });
  const [dOrder, setDOrder] = useState("interactions");
  const [dN, setDN] = useState<number | undefined>();
  const [result, setResult] = useState<{ kind: string; data: Record<string, unknown> } | null>(null);

  const run = useMutation({
    mutationFn: async (kind: string) => {
      if (kind === "orthogonal") {
        const pairs = oaInter.map((s) => s.split("×") as [string, string]);
        return { kind, data: await call<Record<string, unknown>>("POST", "/api/doe/designs/orthogonal", {
          factors: oaFactors.map((f) => ({ name: f.name, levels: parseLevels(f.levels) })), interactions: pairs,
          array: oaArray, persist: true, project_id: projectId }) };
      }
      if (kind === "mixture") {
        return { kind, data: await call<Record<string, unknown>>("POST", "/api/doe/designs/mixture", {
          components: comps.map((c) => ({ name: c.name, low: c.low, high: c.high, cas: c.cas || null })),
          total: 100, mixture_order: mix.order, process_factors: proc.map((p) => ({ ...p, levels: p.levels ?? 3 })),
          categorical_factors: cats.map((c) => ({ name: c.name, levels: String(c.levels).split(/[,，\s]+/).filter(Boolean) })),
          cross: mix.cross, n_runs: mix.n, persist: true, project_id: projectId,
          screen: mix.screen ? { red_lines: { substances: spec?.red_lines ?? [], forbidden_pictograms: spec?.forbidden_pictograms ?? [],
            forbidden_signal_word: spec?.forbidden_signal_word ?? null }, drop_flagged: mix.drop } : null }) };
      }
      return { kind, data: await call<Record<string, unknown>>("POST", "/api/doe/designs/d-optimal", {
        process_factors: proc.map((p) => ({ ...p, levels: p.levels ?? 3 })),
        categorical_factors: cats.map((c) => ({ name: c.name, levels: String(c.levels).split(/[,，\s]+/).filter(Boolean) })),
        order: dOrder, n_runs: dN, persist: true, project_id: projectId }) };
    },
    onSuccess: setResult,
    onError: (e) => message.error(errorText(e)),
  });

  const pairOptions = oaFactors.flatMap((a, i) => oaFactors.slice(i + 1).map((b) => `${a.name}×${b.name}`)).map((v) => ({ value: v, label: v }));
  const procEditor = (
    <>
      <Typography.Title level={5}>{t("doe.process", "工艺因子（连续）")}</Typography.Title>
      <EditableRows<Process> value={proc} onChange={setProc} newRow={() => ({ name: "", low: 0, high: 1, levels: 3 })} columns={[
        { title: t("doe.factor", "因子"), dataIndex: "name" },
        { title: t("doe.low", "下限"), dataIndex: "low", valueType: "digit" },
        { title: t("doe.high", "上限"), dataIndex: "high", valueType: "digit" },
        { title: t("doe.levels_n", "候选水平数"), dataIndex: "levels", valueType: "digit" },
        { title: t("spec.unit", "单位"), dataIndex: "unit" },
      ]} />
      <Typography.Title level={5}>{t("doe.categorical", "分类因子（树脂 / 固化剂牌号等）")}</Typography.Title>
      <EditableRows<Categorical> value={cats} onChange={setCats} newRow={() => ({ name: "", levels: "" })} columns={[
        { title: t("doe.factor", "因子"), dataIndex: "name" },
        { title: t("doe.levels", "水平（逗号分隔）"), dataIndex: "levels" },
      ]} />
    </>
  );
  const model = result?.data.model as Record<string, number | string[]> | undefined;
  const plan = result?.data.plan as { plan_id: string } | undefined;
  const screen = (result?.data.screen as { runs: ScreenRun[]; flagged: number[] } | undefined);

  return (
    <PageContainer subTitle={t("doe.sub", "生成的设计保存为 DOE 计划，可一键下发为实验任务单")}>
      <ProCard bordered>
        <Tabs items={[
          { key: "orthogonal", label: t("doe.orthogonal", "正交表（Taguchi）"), children: (
            <>
              <EditableRows<LevelFactor> value={oaFactors} onChange={setOaFactors} newRow={() => ({ name: "", levels: "" })} columns={[
                { title: t("doe.factor", "因子"), dataIndex: "name" },
                { title: t("doe.levels", "水平（逗号分隔）"), dataIndex: "levels" },
              ]} />
              <Space style={{ marginTop: 12 }} wrap>
                <span>{t("doe.interactions", "交互作用")}</span>
                <Select mode="multiple" style={{ minWidth: 260 }} value={oaInter} onChange={setOaInter} options={pairOptions} />
                <span>{t("doe.array", "指定正交表")}</span>
                <Select allowClear style={{ width: 160 }} value={oaArray} onChange={setOaArray} placeholder={t("doe.auto", "自动选择")}
                  options={(arrays.data ?? []).map((a) => ({ value: a.name, label: `${a.name}（${a.runs} 次）` }))} />
                <Button type="primary" loading={run.isPending} onClick={() => run.mutate("orthogonal")}>{t("doe.generate", "生成设计")}</Button>
              </Space>
            </>
          ) },
          { key: "mixture", label: t("doe.mixture", "约束混料 + D-最优"), children: (
            <>
              <EditableRows<Component> value={comps} onChange={setComps} newRow={() => ({ name: "", low: 0, high: 100 })} columns={[
                { title: t("doe.component", "组分"), dataIndex: "name" },
                { title: t("doe.low_pct", "下限 %"), dataIndex: "low", valueType: "digit" },
                { title: t("doe.high_pct", "上限 %"), dataIndex: "high", valueType: "digit" },
                { title: "CAS", dataIndex: "cas" },
              ]} />
              {procEditor}
              <Form layout="inline" style={{ marginTop: 12 }}>
                <Form.Item label={t("doe.model", "混料模型")}><Select style={{ width: 160 }} value={mix.order} onChange={(v) => setMix({ ...mix, order: v })}
                  options={[{ value: "linear", label: t("doe.linear", "线性") }, { value: "quadratic", label: t("doe.quadratic", "二次") }, { value: "special_cubic", label: t("doe.cubic", "特殊三次") }]} /></Form.Item>
                <Form.Item label={t("doe.cross", "与工艺因子")}><Select style={{ width: 140 }} value={mix.cross} onChange={(v) => setMix({ ...mix, cross: v })}
                  options={[{ value: "full", label: t("doe.cross_full", "完全交叉") }, { value: "additive", label: t("doe.cross_add", "加性") }]} /></Form.Item>
                <Form.Item label={t("doe.n_runs", "试验次数")}><InputNumber min={2} max={200} value={mix.n} onChange={(v) => setMix({ ...mix, n: v ?? undefined })} placeholder={t("doe.auto", "自动选择")} /></Form.Item>
                <Form.Item label={t("doe.screen", "合规预筛")}><Switch checked={mix.screen} onChange={(v) => setMix({ ...mix, screen: v })} /></Form.Item>
                <Form.Item label={t("doe.drop", "剔除不合规点")}><Switch checked={mix.drop} onChange={(v) => setMix({ ...mix, drop: v })} /></Form.Item>
                <Button type="primary" loading={run.isPending} onClick={() => run.mutate("mixture")}>{t("doe.generate", "生成设计")}</Button>
              </Form>
            </>
          ) },
          { key: "doptimal", label: t("doe.d_optimal", "D-最优（工艺 / 分类因子）"), children: (
            <>
              {procEditor}
              <Space style={{ marginTop: 12 }}>
                <Select style={{ width: 200 }} value={dOrder} onChange={setDOrder} options={[
                  { value: "linear", label: t("doe.main", "主效应") }, { value: "interactions", label: t("doe.inter", "主效应 + 交互") }, { value: "quadratic", label: t("doe.quad", "二次响应面") }]} />
                <InputNumber min={2} max={200} value={dN} onChange={(v) => setDN(v ?? undefined)} placeholder={t("doe.n_runs", "试验次数")} />
                <Button type="primary" loading={run.isPending} onClick={() => run.mutate("doptimal")}>{t("doe.generate", "生成设计")}</Button>
              </Space>
            </>
          ) },
        ]} />
      </ProCard>
      {result && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("doe.result", "设计结果")} extra={plan && <DispatchButton planId={plan.plan_id} />}>
          {"array" in result.data && <Descriptions size="small" column={3}>
            <Descriptions.Item label={t("doe.array", "正交表")}>{String(result.data.array)}</Descriptions.Item>
            <Descriptions.Item label={t("doe.assignment", "列分配")}>{JSON.stringify(result.data.assignment)}</Descriptions.Item>
            <Descriptions.Item label={t("doe.interactions", "交互作用")}>{JSON.stringify(result.data.interactions)}</Descriptions.Item>
          </Descriptions>}
          {model && <Descriptions size="small" column={4}>
            <Descriptions.Item label={t("doe.params", "模型参数")}>{String(model.parameters)}</Descriptions.Item>
            <Descriptions.Item label={t("doe.d_eff", "D 效率（相对候选集）")}>{Number(model.d_efficiency_vs_candidates ?? model.d_efficiency).toFixed(1)}%</Descriptions.Item>
            <Descriptions.Item label={t("doe.max_var", "最大预测方差")}>{Number(model.max_prediction_variance).toFixed(2)}</Descriptions.Item>
            <Descriptions.Item label={t("doe.runs", "试验次数")}>{String(model.runs)}</Descriptions.Item>
          </Descriptions>}
          {(result.data.notes as string[] | undefined)?.map((n, i) => <Alert key={i} type="info" showIcon message={n} style={{ marginBottom: 8 }} />)}
          {screen && screen.flagged.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 8 }}
            message={t("doe.flagged", "{{n}} 个试验点命中合规红线：{{runs}}", { n: screen.flagged.length, runs: screen.flagged.join(", ") })} />}
          <ErrorAlert error={run.error} />
          <RunsTable runs={result.data.runs as Run[]} screen={screen?.runs} />
        </ProCard>
      )}
    </PageContainer>
  );
}
