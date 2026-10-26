import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Checkbox, Descriptions, Form, InputNumber, List, Progress, Table, Tag, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { awaitTask, cancelTask, type AsyncTaskAccepted, type TaskProgressEvent } from "@/api/stream";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { HandoffBanner } from "@/shared/handoff";
import { defaultConstraints, defaultObjectives, distinctCompositions, frontier, primaryMetric } from "./logic";
import type { DesignCandidate, HardConstraint, InverseDesignResult, ObjectiveSpec } from "./types";
import { useRequirement } from "./useRequirement";

const METRICS = ["salt_spray_hours", "cost_cny_per_kg", "voc_gpl", "cleaning_efficiency", "coating_weight_gsm", "film_weight_gsm", "adhesion_mpa"];

/** Inverse design (legacy InverseDesignModal): hard constraints (pass/fail gates) + soft objectives → Pareto set of formulations. */
export default function InverseDesignPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const { projectId, requirement } = useRequirement();
  const domain = requirement?.domain ?? "anticorrosion_coating";
  const [hard, setHard] = useState<HardConstraint[]>(() => defaultConstraints(domain));
  const [soft, setSoft] = useState<ObjectiveSpec[]>(() => defaultObjectives(domain));
  const [population, setPopulation] = useState(48);
  const [generations, setGenerations] = useState(30);
  const [seedWithLlm, setSeedWithLlm] = useState(true);
  const [ev, setEv] = useState<TaskProgressEvent | null>(null);
  const [result, setResult] = useState<InverseDesignResult | null>(null);
  const taskId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  // Reset the defaults when the project (and so the domain) changes; project objectives become the soft objectives.
  useEffect(() => {
    setHard(defaultConstraints(domain));
    const objs = (requirement?.objectives ?? []).filter((o) => o.direction !== "match_target");
    setSoft(objs.length ? objs.map((o) => ({ metric: o.metric, direction: o.direction, weight: o.weight ?? 1 / objs.length })) : defaultObjectives(domain));
    setResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, domain]);
  useEffect(() => () => abort.current?.abort(), []);

  const metricEnum = Object.fromEntries(METRICS.map((m) => [m, { text: metricLabel(m) }]));
  function metricLabel(m: string) {
    return ({
      salt_spray_hours: t("design.m.salt_spray_hours", "耐盐雾 (h)"), cost_cny_per_kg: t("design.m.cost_cny_per_kg", "成本 (CNY/kg)"),
      voc_gpl: t("design.m.voc_gpl", "VOC (g/L)"), cleaning_efficiency: t("design.m.cleaning_efficiency", "清洗率 (%)"),
      coating_weight_gsm: t("design.m.coating_weight_gsm", "膜重 (g/m²)"), film_weight_gsm: t("design.m.film_weight_gsm", "干膜重 (g/m²)"),
      adhesion_mpa: t("design.m.adhesion_mpa", "附着力 (MPa)"),
    } as Record<string, string>)[m] ?? m.replace(/_/g, " ");
  }

  const run = useMutation({
    mutationFn: async () => {
      if (!requirement) throw new Error(t("design.need_project", "请先在顶部选择项目。"));
      setResult(null);
      setEv(null);
      abort.current = new AbortController();
      const acc = await call<AsyncTaskAccepted>("POST", "/api/design/inverse", {
        requirement, targets: { hard, soft }, population, generations, seed_with_llm: seedWithLlm,
      });
      taskId.current = acc.task_id;
      const fin = await awaitTask(acc.task_id, { onEvent: setEv, signal: abort.current.signal });
      const data = fin.data as InverseDesignResult | undefined;
      if (!data || data.error) throw new Error(data?.error || t("design.inv.failed", "搜索失败"));
      return data;
    },
    onSuccess: setResult,
    onSettled: () => { taskId.current = null; },
  });
  const stop = async () => {
    if (taskId.current) await cancelTask(taskId.current).catch(() => undefined);
    abort.current?.abort();
  };

  const save = useMutation({
    mutationFn: async (cands: DesignCandidate[]) => {
      for (const c of cands) {
        await call("POST", "/api/formulations/versions", { formulation: c.formulation, project_id: projectId,
          change_summary: t("design.inv.saved_summary", "逆向设计候选（帕累托层级 {{r}}）", { r: c.pareto_rank ?? "—" }) });
      }
      return cands.length;
    },
    onSuccess: (n) => message.success(t("design.inv.saved", "已保存 {{n}} 个候选为配方版本", { n })),
    onError: (e) => message.error(errorText(e)),
  });
  const toWorkbench = useMutation({
    mutationFn: (plan: unknown) => call<{ campaign_id: number }>("POST", "/api/experiments/workbench/campaigns", {
      plan, strategy: "DOE-verification", requirement, project_id: projectId }),
    onSuccess: (wb) => message.success(t("design.doe.adopted", "已创建实验台账 #{{id}}", { id: wb.campaign_id })),
    onError: (e) => message.error(errorText(e)),
  });

  const cands = result?.candidates ?? [];
  const pct = ev?.progress != null ? Math.round(ev.progress <= 1 ? ev.progress * 100 : ev.progress) : undefined;

  return (
    <PageContainer subTitle={t("design.inv.sub", "给定目标性能，反向搜索配方：硬约束必须满足，软目标之间权衡，返回帕累托前沿上成分组成互不相同的候选")}>
      <HandoffBanner />
      {!projectId && <Alert type="info" showIcon style={{ marginBottom: 16 }} message={t("design.need_project", "请先在顶部选择项目。")} />}
      <ProCard bordered split="vertical" wrap>
        <ProCard colSpan={{ xs: 24, lg: 14 }} title={t("design.inv.hard", "硬约束 · 必须满足")}>
          <EditableRows<HardConstraint> value={hard} onChange={setHard} addLabel={t("design.inv.add_constraint", "添加约束")}
            newRow={() => ({ metric: "cost_cny_per_kg", op: "le", value: 50 })} columns={[
              { title: t("design.inv.metric", "指标"), dataIndex: "metric", valueType: "select", valueEnum: metricEnum, width: 180 },
              { title: t("design.inv.op", "条件"), dataIndex: "op", valueType: "select", width: 120, valueEnum: {
                ge: { text: t("design.inv.ge", "≥ 不低于") }, le: { text: t("design.inv.le", "≤ 不超过") }, between: { text: t("design.inv.between", "介于") } } },
              { title: t("design.inv.value", "值"), dataIndex: "value", valueType: "digit" },
              { title: t("design.inv.value_max", "上界（介于）"), dataIndex: "value_max", valueType: "digit" },
            ]} />
        </ProCard>
        <ProCard colSpan={{ xs: 24, lg: 10 }} title={t("design.inv.soft", "软目标 · 权衡")}>
          <EditableRows<ObjectiveSpec> value={soft} onChange={setSoft} newRow={() => ({ metric: "cost_cny_per_kg", direction: "minimize", weight: 0.5 })}
            columns={[
              { title: t("design.inv.metric", "指标"), dataIndex: "metric", valueType: "select", valueEnum: metricEnum },
              { title: t("design.inv.direction", "方向"), dataIndex: "direction", valueType: "select", width: 110, valueEnum: {
                maximize: { text: t("design.inv.max", "最大化") }, minimize: { text: t("design.inv.min", "最小化") } } },
              { title: t("design.inv.weight", "权重"), dataIndex: "weight", valueType: "digit", width: 90 },
            ]} />
        </ProCard>
      </ProCard>
      <ProCard bordered style={{ marginTop: 16 }}>
        <Form layout="inline" style={{ rowGap: 12 }}>
          <Form.Item label={t("design.inv.population", "种群")}><InputNumber min={8} max={400} value={population} onChange={(v) => setPopulation(v ?? 48)} /></Form.Item>
          <Form.Item label={t("design.inv.generations", "迭代代数")}><InputNumber min={1} max={300} value={generations} onChange={(v) => setGenerations(v ?? 30)} /></Form.Item>
          <Form.Item><Checkbox checked={seedWithLlm} onChange={(e) => setSeedWithLlm(e.target.checked)}>{t("design.inv.llm_seed", "用 LLM 播种初始种群")}</Checkbox></Form.Item>
          <Button type="primary" loading={run.isPending} disabled={!requirement || hard.length === 0 || !can("optimize.run")} onClick={() => run.mutate()}>
            {t("design.inv.run", "开始逆向设计")}</Button>
          {run.isPending && <Button danger onClick={() => void stop()} style={{ marginLeft: 8 }}>{t("design.stop", "停止")}</Button>}
        </Form>
        {run.isPending && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">{ev?.message || t("design.inv.searching", "搜索中…")}</Typography.Text>
            {pct != null && <Progress percent={pct} size="small" />}
          </div>
        )}
        <div style={{ marginTop: 12 }}><ErrorAlert error={run.error} /></div>
      </ProCard>

      {result && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("design.inv.result", "候选配方")}
          extra={<Button disabled={!cands.length || !can("lab.execute")} loading={save.isPending} onClick={() => save.mutate(frontier(cands))}>
            {t("design.inv.save_front", "保存前沿候选为配方版本")}</Button>}>
          <Descriptions size="small" column={{ xs: 1, md: 5 }} style={{ marginBottom: 12 }}>
            <Descriptions.Item label={t("design.inv.engine", "引擎")}>{result.engine}</Descriptions.Item>
            <Descriptions.Item label={t("design.inv.generations", "迭代代数")}>{result.generations}</Descriptions.Item>
            <Descriptions.Item label={t("design.inv.evaluations", "评估次数")}>{result.evaluations}</Descriptions.Item>
            <Descriptions.Item label={t("design.inv.rejected", "淘汰不可行")}>{result.rejected_infeasible}</Descriptions.Item>
            <Descriptions.Item label={t("design.inv.distinct", "不同成分组成")}>{distinctCompositions(cands)}</Descriptions.Item>
          </Descriptions>
          {result.warnings.map((w) => <Alert key={w} type="warning" showIcon message={w} style={{ marginBottom: 8 }} />)}
          <Table<DesignCandidate & { _k: number }> size="small" rowKey="_k" pagination={false} scroll={{ x: true }}
            dataSource={cands.slice(0, 12).map((c, i) => ({ ...c, _k: i }))}
            expandable={{ expandedRowRender: (c) => (
              <Table size="small" rowKey={(r) => r.name} pagination={false} dataSource={c.formulation.ingredients}
                columns={[{ title: t("design.ingredient", "成分"), dataIndex: "name" }, { title: t("design.role", "角色"), dataIndex: "role" },
                  { title: "wt%", dataIndex: "weight_pct", align: "right", render: (v: number) => Number(v.toFixed(2)) }]} />) }}
            columns={[
              { title: t("design.inv.rank", "层级"), width: 90, render: (_v, c) => (c.pareto_rank === 0
                ? <Tag color="blue">{t("design.inv.front", "前沿")}</Tag> : `rank ${c.pareto_rank ?? "—"}`) },
              { title: t("design.inv.state", "状态"), width: 120, render: (_v, c) => (c.feasible ? <Tag color="green">{t("design.inv.ok", "满足")}</Tag>
                : <Tag color="red">{t("design.inv.violation", "违反 {{v}}", { v: c.violation.toFixed(2) })}</Tag>) },
              { title: t("design.inv.materials", "成分组成"), ellipsis: true, render: (_v, c) => c.materials.join("、") },
              { title: t("design.inv.primary", "主指标"), align: "right", render: (_v, c) => { const p = primaryMetric(c.formulation.predicted); return p != null ? p.toFixed(0) : "—"; } },
              { title: t("design.m.cost", "成本"), align: "right", render: (_v, c) => c.formulation.predicted?.cost_cny_per_kg?.toFixed(1) ?? "—" },
              { title: "VOC", align: "right", render: (_v, c) => c.formulation.predicted?.voc_gpl?.toFixed(0) ?? "—" },
              { title: t("common.actions", "操作"), width: 90, render: (_v, c) => <a onClick={() => save.mutate([c])}>{t("common.save", "保存")}</a> },
            ]} />
          {(result.tradeoff?.verification_does?.length ?? 0) > 0 && (
            <>
              <Typography.Title level={5} style={{ marginTop: 16 }}>{t("design.inv.verify", "验证 DOE（一键写入实验台账验证预测）")}</Typography.Title>
              <List size="small" bordered dataSource={result.tradeoff!.verification_does!} rowKey="candidate_id" renderItem={(v) => (
                <List.Item actions={[<a key="a" onClick={() => toWorkbench.mutate(v.doe_plan)}>{t("design.doe.adopt", "写入实验台账")}</a>]}>
                  <List.Item.Meta title={v.candidate_name}
                    description={<>{v.note}<br />{t("design.inv.verify_runs", "{{n}} 个实验点 · {{d}}", { n: v.doe_plan.runs.length, d: v.doe_plan.design })}</>} />
                </List.Item>)} />
            </>
          )}
        </ProCard>
      )}
    </PageContainer>
  );
}
