import { PageContainer, ProCard } from "@ant-design/pro-components";
import {
  Alert, App, Button, Descriptions, Drawer, Empty, Form, InputNumber, List, Progress, Select, Space, Table, Tag, Tooltip, Typography, Upload,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { awaitTask, thinkingSteps, type AsyncTaskAccepted, type TaskProgressEvent } from "@/api/stream";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { DispatchButton } from "@/features/lab/DispatchButton";
import { LeversPanel } from "./LeversPanel";
import { AI_DESIGN, designsForEngine, strategyForPlan, type DoeEngine } from "./logic";
import type { ActiveDoeResult, DOEPlan, ModelInfo, TrainingReport, WorkbenchCampaignSummary } from "./types";
import { useRequirement } from "./useRequirement";

type AlEngine = "auto" | "legacy" | "baybe";
type CcdAlpha = "face" | "inscribed" | "rotatable";
interface Meta { engines?: Record<string, { available: boolean; label: string }> }

/** Coloured R² dial of one surrogate model (legacy R2Gauge). Negative R² shows N/A. */
function ModelCard({ m }: { m: ModelInfo }) {
  const { t } = useTranslation();
  const pct = Math.max(0, Math.min(1, m.r2)) * 100;
  const status = m.r2 > 0.85 ? "success" : m.r2 > 0.6 ? "normal" : "exception";
  return (
    <ProCard bordered size="small" colSpan={{ xs: 24, sm: 12, md: 8, xl: 6 }}>
      <Space align="center">
        <Progress type="dashboard" size={64} percent={Math.round(pct)} status={status}
          format={() => (m.r2 < 0 ? "N/A" : `${Math.round(pct)}%`)} />
        <div>
          <Typography.Text code>{m.metric}</Typography.Text>
          {m.pinned && <Tag color="gold" style={{ marginLeft: 4 }}>{t("design.doe.pinned", "已锁定")}</Tag>}
          <div><Typography.Text type="secondary">{m.backend} · n={m.n_samples}</Typography.Text></div>
          <div><Typography.Text type="secondary">
            RMSE={m.rmse.toFixed(m.rmse < 1 ? 3 : 1)}{m.cv_r2 != null ? ` · cvR²=${m.cv_r2.toFixed(2)}` : ""}
          </Typography.Text></div>
        </div>
      </Space>
    </ProCard>
  );
}

/** Classic DOE (legacy DoeResultsPanel): full factorial / CCD / Box-Behnken / LHS … over the project levers, AI active
 *  learning, the async closed-loop cycle, worksheet export / CSV import and the surrogate-model R² board. */
export default function ClassicDoePage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const { projectId, requirement, levers } = useRequirement();
  const [engine, setEngine] = useState<DoeEngine>("auto");
  const [alEngine, setAlEngine] = useState<AlEngine>("auto");
  const [design, setDesign] = useState("ccd");
  const [ccdAlpha, setCcdAlpha] = useState<CcdAlpha>("face");
  const [n, setN] = useState<number | null>(null);
  const [seed, setSeed] = useState<number | null>(null);
  const [nSuggest, setNSuggest] = useState(4);
  const [campaignId, setCampaignId] = useState<number | undefined>();
  const [campaignState, setCampaignState] = useState<string | null>(null);
  const [plan, setPlan] = useState<DOEPlan | null>(null);
  const [active, setActive] = useState<ActiveDoeResult | null>(null);
  const [leversOpen, setLeversOpen] = useState(false);
  const [cycleEv, setCycleEv] = useState<TaskProgressEvent | null>(null);
  const [trainMsg, setTrainMsg] = useState("");

  const meta = useQuery({ queryKey: ["meta"], queryFn: () => call<Meta>("GET", "/api/meta"), staleTime: 300_000 });
  const engineOk = (id: string) => meta.data?.engines?.[id]?.available !== false;
  const campaigns = useQuery({ queryKey: ["workbench", "campaigns"],
    queryFn: () => call<WorkbenchCampaignSummary[]>("GET", "/api/experiments/workbench/campaigns") });
  const projectCampaigns = (campaigns.data ?? []).filter((c) => !projectId || c.project_id === projectId);
  const models = useQuery({ queryKey: ["models"], queryFn: () => call<ModelInfo[]>("GET", "/api/models") });
  const shownModels = (models.data ?? []).filter((m) => (!requirement || m.domain === requirement.domain)
    && (!m.project_id || !projectId || m.project_id === projectId));

  const designs = designsForEngine(engine);
  const shownDesign = designs.includes(design) ? design : designs[0];
  const designLabel = (d: string) => ({
    full_factorial: t("design.doe.d.full_factorial", "全因子"), fractional_factorial: t("design.doe.d.fractional_factorial", "部分因子"),
    plackett_burman: "Plackett-Burman", ccd: t("design.doe.d.ccd", "中心复合 CCD"), lhs: t("design.doe.d.lhs", "拉丁超立方 LHS"),
    bbdesign: "Box-Behnken (pyDOE)", simplex_lattice: t("design.doe.d.simplex", "混合物 simplex (pyDOE)"),
    sobol: t("design.doe.d.sobol", "Sobol 序列 (pyDOE)"), ai_active: t("design.doe.d.ai_active", "AI 主动选点"),
  } as Record<string, string>)[d] ?? d;

  const generate = useMutation({
    mutationFn: async () => {
      if (!requirement) throw new Error(t("design.need_project", "请先在顶部选择项目。"));
      if (shownDesign === AI_DESIGN) {
        const r = await call<ActiveDoeResult>("POST", "/api/doe/active", {
          ...requirement, n_suggest: nSuggest, doe_design: "lhs", engine: alEngine, doe_engine: engine,
          campaign_state: campaignState, workbench_campaign_id: campaignId ?? null, seed,
        });
        return { plan: r.plan, active: r };
      }
      const q = new URLSearchParams({ design: shownDesign, engine });
      if (shownDesign === "ccd") q.set("ccd_alpha", ccdAlpha);
      if (n != null) q.set("n", String(n));
      if (seed != null) q.set("seed", String(seed));
      return { plan: await call<DOEPlan>("POST", `/api/doe?${q}`, requirement), active: null };
    },
    onSuccess: ({ plan: p, active: a }) => {
      setPlan(p);
      setActive(a);
      if (a) setCampaignState(a.campaign_state ?? campaignState);
      void qc.invalidateQueries({ queryKey: ["design", "doe-history"] });
    },
  });

  const adopt = useMutation({
    mutationFn: async (p: DOEPlan) => {
      const wb = await call<{ campaign_id: number }>("POST", "/api/experiments/workbench/campaigns", {
        plan: p, strategy: strategyForPlan(p.design, active?.engine), requirement, project_id: projectId,
      });
      let warn = "";
      if (p.plan_id) await call("POST", `/api/doe/${encodeURIComponent(p.plan_id)}/activate`, {}).catch((e) => { warn = errorText(e); });
      return { id: wb.campaign_id, warn };
    },
    onSuccess: ({ id, warn }) => {
      setCampaignId(id);
      message.success(t("design.doe.adopted", "已创建实验台账 #{{id}}", { id }));
      if (warn) message.warning(t("design.doe.activate_failed", "DOE 计划状态同步失败（计划已采纳）：{{e}}", { e: warn }));
      void qc.invalidateQueries({ queryKey: ["workbench", "campaigns"] });
      void qc.invalidateQueries({ queryKey: ["design", "doe-history"] });
    },
    onError: (e) => message.error(errorText(e)),
  });

  const cycle = useMutation({
    mutationFn: async () => {
      if (!requirement) throw new Error(t("design.need_project", "请先在顶部选择项目。"));
      setCycleEv(null);
      const acc = await call<AsyncTaskAccepted>("POST", "/api/doe/cycle", { requirement, workbench_campaign_id: campaignId ?? null });
      const fin = await awaitTask(acc.task_id, { onEvent: setCycleEv, timeoutMs: 5 * 60_000 });
      const d = fin.data as { status?: string; message?: string; count?: number; paused?: boolean } | undefined;
      if (d?.paused) throw new Error(d.message || t("design.doe.cycle_paused", "DOE 闭环已暂停"));
      if (d?.status === "error") throw new Error(d.message || t("design.doe.cycle_failed", "DOE 闭环失败"));
      return d?.message || (d?.count != null ? t("design.doe.cycle_count", "DOE 闭环已生成 {{n}} 条待测实验", { n: d.count })
        : t("design.doe.cycle_done", "DOE 闭环完成"));
    },
    onSuccess: (msg) => { message.success(msg); void qc.invalidateQueries({ queryKey: ["design", "doe-history"] }); },
  });

  const importCsv = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return call<TrainingReport>("POST", `/api/experiments/import-csv${requirement ? `?domain=${requirement.domain}` : ""}`, fd);
    },
    onSuccess: (r) => { setTrainMsg(r.message); void qc.invalidateQueries({ queryKey: ["models"] }); },
    onError: (e) => message.error(t("design.doe.import_failed", "CSV 导入失败：{{e}}", { e: errorText(e) })),
  });

  const exportPlan = (fmt: "csv" | "xlsx") => plan && download(`/api/doe/${encodeURIComponent(plan.plan_id)}/export?format=${fmt}`,
    `doe_${plan.plan_id}.${fmt}`).catch((e) => message.error(errorText(e)));

  const explainByRun = new Map((active?.run_explanations ?? []).map((e) => [e.run_id, e]));
  const steps = thinkingSteps(cycleEv);

  return (
    <PageContainer
      subTitle={t("design.doe.sub", "经典 DOE：全因子 / 部分因子 / Plackett-Burman / CCD / LHS / Box-Behnken / Sobol 与 AI 主动选点，基于项目 levers")}
      extra={[
        <Link key="h" to="/lab/doe/history"><Button>{t("design.doe.history", "DOE 历史")}</Button></Link>,
        <Link key="d" to="/lab/doe"><Button>{t("design.doe.designer", "正交 / 混料 / D-最优")}</Button></Link>,
      ]}>
      {!projectId && <Alert type="info" showIcon style={{ marginBottom: 16 }} message={t("design.need_project", "请先在顶部选择项目。")} />}
      <ProCard bordered title={t("design.doe.setup", "设计参数")}
        extra={<Button onClick={() => setLeversOpen(true)} disabled={!projectId}>
          {t("design.doe.levers", "因子 Levers（{{n}}）", { n: levers.length })}</Button>}>
        {levers.length === 0 && projectId && <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={t("design.doe.no_levers", "项目未定义 levers，将自动从当前配方推导因子。")} />}
        <Form layout="inline" style={{ rowGap: 12 }}>
          <Form.Item label={t("design.doe.engine", "DOE 引擎")}>
            <Select style={{ width: 170 }} value={engine} onChange={setEngine} options={[
              { value: "auto", label: t("design.doe.auto", "自动") }, { value: "native", label: t("design.doe.native", "经典 native") },
              { value: "pydoe", disabled: !engineOk("pydoe"), label: engineOk("pydoe") ? t("design.doe.pydoe", "增强 pydoe")
                : t("design.doe.pydoe_missing", "增强 pydoe（未安装）") }]} />
          </Form.Item>
          <Form.Item label={t("design.doe.design", "设计")}>
            <Select style={{ width: 210 }} value={shownDesign} onChange={setDesign} options={designs.map((d) => ({ value: d, label: designLabel(d) }))} />
          </Form.Item>
          {shownDesign === "ccd" && (
            <Form.Item label={t("design.doe.ccd_alpha", "星点")} tooltip={t("design.doe.ccd_alpha_tip",
              "面心 α=1 时所有实验点都在因子范围内；内切 = 旋转设计整体内缩（仍全部在范围内）；旋转设计的星点超出范围，对应实验会被标为不可行")}>
              <Select style={{ width: 240 }} value={ccdAlpha} onChange={setCcdAlpha} options={[
                { value: "face", label: t("design.doe.alpha_face", "面心 α=1（全部在范围内）") },
                { value: "inscribed", label: t("design.doe.alpha_inscribed", "内切（旋转 + 全在范围内）") },
                { value: "rotatable", label: t("design.doe.alpha_rotatable", "旋转 α=n^¼（越界·标不可行）") }]} />
            </Form.Item>
          )}
          {shownDesign === AI_DESIGN ? (
            <>
              <Form.Item label={t("design.doe.al_engine", "主动学习引擎")}>
                <Select style={{ width: 200 }} value={alEngine} onChange={setAlEngine} options={[
                  { value: "auto", label: t("design.doe.auto", "自动") }, { value: "legacy", label: t("design.doe.legacy_ei", "经典 EI") },
                  { value: "baybe", disabled: !engineOk("baybe"), label: engineOk("baybe") ? "baybe Campaign"
                    : t("design.doe.baybe_missing", "baybe Campaign（未安装）") }]} />
              </Form.Item>
              <Form.Item label={t("design.doe.n_suggest", "建议点数")}>
                <InputNumber min={1} max={50} value={nSuggest} onChange={(v) => setNSuggest(v ?? 4)} />
              </Form.Item>
            </>
          ) : (
            <Form.Item label={t("design.doe.n", "实验数")}>
              <InputNumber min={2} max={200} value={n} onChange={setN} placeholder={t("design.doe.auto", "自动")} />
            </Form.Item>
          )}
          <Form.Item label={t("design.doe.seed", "随机种子")} tooltip={t("design.doe.seed_tip", "指定后 LHS 等随机设计可复现")}>
            <InputNumber value={seed} onChange={setSeed} placeholder="—" />
          </Form.Item>
          <Form.Item label={t("design.doe.campaign", "实验台账")}>
            <Select allowClear style={{ width: 220 }} value={campaignId} onChange={setCampaignId} loading={campaigns.isLoading}
              placeholder={t("design.doe.campaign_none", "无（新建）")}
              options={projectCampaigns.map((c) => ({ value: c.id, label: `#${c.id} ${c.name} · ${c.row_count}` }))} />
          </Form.Item>
        </Form>
        <Space wrap style={{ marginTop: 16 }}>
          <Button type="primary" loading={generate.isPending} disabled={!requirement} onClick={() => generate.mutate()}>
            {t("design.doe.generate", "生成 DOE")}</Button>
          <Tooltip title={t("design.doe.cycle_tip", "提交异步 DOE 闭环任务（Baybe/LHS → 待测实验），写入所选实验台账")}>
            <Button loading={cycle.isPending} disabled={!requirement} onClick={() => cycle.mutate()}>{t("design.doe.cycle", "运行 DOE 闭环")}</Button>
          </Tooltip>
          <Upload accept=".csv,text/csv" showUploadList={false} beforeUpload={(f) => { importCsv.mutate(f); return false; }}>
            <Button loading={importCsv.isPending}>{t("design.doe.import", "导入实测 CSV 并训练")}</Button>
          </Upload>
        </Space>
        <div style={{ marginTop: 12 }}>
          <ErrorAlert error={generate.error} />
          <ErrorAlert error={cycle.error} />
          {cycle.isPending && cycleEv && (
            <Alert type="info" showIcon message={cycleEv.message || t("design.doe.cycle_running", "闭环运行中…")}
              description={<>
                {typeof cycleEv.progress === "number" && <Progress percent={Math.round(cycleEv.progress * (cycleEv.progress <= 1 ? 100 : 1))} size="small" />}
                {steps.length > 0 && <List size="small" dataSource={steps} rowKey="id" renderItem={(s) => (
                  <List.Item><Space><Tag color={s.status === "error" ? "red" : s.status === "done" ? "green" : "blue"}>{s.status ?? "…"}</Tag>
                    {s.title}{s.detail && <Typography.Text type="secondary">{s.detail}</Typography.Text>}</Space></List.Item>)} />}
              </>} />
          )}
          {trainMsg && <Alert type="success" showIcon message={trainMsg} closable onClose={() => setTrainMsg("")} />}
        </div>
      </ProCard>

      {plan ? (
        <ProCard bordered style={{ marginTop: 16 }} title={t("design.doe.plan", "实验矩阵 · {{d}}", { d: designLabel(plan.design) })}
          extra={<Space wrap>
            <Button onClick={() => exportPlan("csv")}>{t("design.doe.export_csv", "导出 CSV")}</Button>
            <Button onClick={() => exportPlan("xlsx")}>XLSX</Button>
            <Button loading={adopt.isPending} onClick={() => adopt.mutate(plan)}>{t("design.doe.adopt", "写入实验台账")}</Button>
            {plan.plan_id && <DispatchButton planId={plan.plan_id} />}
          </Space>}>
          {plan.notes && <Typography.Paragraph type="secondary" style={{ whiteSpace: "pre-wrap" }}>{plan.notes}</Typography.Paragraph>}
          {active && (
            <>
              <Descriptions size="small" column={{ xs: 1, md: 3 }} style={{ marginBottom: 12 }}>
                <Descriptions.Item label={t("design.doe.al_engine", "主动学习引擎")}>{active.engine}</Descriptions.Item>
                {active.strategy_label && <Descriptions.Item label={t("design.doe.strategy", "策略")}>
                  <Tag color={active.strategy_label === "exploration" ? "blue" : active.strategy_label === "exploitation" ? "green" : "purple"}>
                    {active.strategy_label}</Tag></Descriptions.Item>}
                {active.recommended_next_action && <Descriptions.Item label={t("design.doe.next_action", "建议下一步")}>
                  {active.recommended_next_action}</Descriptions.Item>}
              </Descriptions>
              {active.strategy_rationale && <Alert type="info" showIcon style={{ marginBottom: 8 }} message={active.strategy_rationale} />}
              {[active.chemical_feasibility, active.physical_constraints].map((v, i) => v && !v.feasible && (
                <Alert key={i} type="warning" showIcon style={{ marginBottom: 8 }} message={`${v.status}: ${v.reasons.join("；")}`} />
              ))}
              {(active.anomalies ?? []).map((a) => (
                <Alert key={`${a.experiment_id}-${a.type}`} type={a.severity === "critical" ? "error" : a.severity === "warning" ? "warning" : "info"}
                  showIcon style={{ marginBottom: 8 }} message={`${t("design.doe.anomaly", "异常")} #${a.experiment_id} · ${a.type}`} description={a.note} />
              ))}
            </>
          )}
          <Table size="small" rowKey="run_id" dataSource={plan.runs} pagination={plan.runs.length > 50 ? { pageSize: 50 } : false} scroll={{ x: true }}
            columns={[
              { title: "#", dataIndex: "run_id", width: 110, fixed: "left", render: (_v, r) => (
                <Space size={4}>{r.run_id}
                  {r.ai_suggested && <Tag color="purple">AI</Tag>}
                  {r.infeasible && <Tooltip title={r.infeasible_reason || t("design.doe.incompatible", "知识图谱检测到材料不相容")}>
                    <Tag color="red">{t("design.doe.infeasible", "不可行")}</Tag></Tooltip>}
                </Space>) },
              ...plan.factors.map((f) => ({ title: `${f.name}${f.unit ? ` (${f.unit})` : ""}`, key: f.name, align: "right" as const,
                render: (_v: unknown, r: (typeof plan.runs)[number]) => {
                  const v = r.natural[f.name];
                  return typeof v === "number" ? Number(v.toFixed(4)) : String(v ?? "—");
                } })),
              ...(active?.run_explanations?.length ? [{ title: t("design.doe.why", "选点理由"), key: "why", width: 320,
                render: (_v: unknown, r: (typeof plan.runs)[number]) => {
                  const e = explainByRun.get(r.run_id);
                  return e ? <Tooltip title={(e.constraint_warnings ?? []).join("；") || undefined}>
                    <Typography.Text>{e.summary}</Typography.Text></Tooltip> : null;
                } }] : []),
            ]} />
        </ProCard>
      ) : (
        <ProCard bordered style={{ marginTop: 16 }}>
          <Empty description={t("design.doe.empty", "生成 DOE 设计后，可写入实验台账或下发为实验任务，填报实测后导入 CSV 回灌训练。")} />
        </ProCard>
      )}

      <ProCard bordered style={{ marginTop: 16 }} title={t("design.doe.models", "代理模型 R²")} loading={models.isLoading}
        extra={<Button size="small" onClick={() => void models.refetch()}>{t("design.refresh", "刷新")}</Button>} wrap gutter={[12, 12]}>
        {models.error ? <ErrorAlert error={models.error} /> : shownModels.length === 0
          ? <Empty description={t("design.doe.no_models", "暂无已训练模型")} />
          : shownModels.map((m) => <ModelCard key={`${m.domain}-${m.project_id ?? ""}-${m.metric}`} m={m} />)}
      </ProCard>

      <Drawer open={leversOpen} onClose={() => setLeversOpen(false)} width={Math.min(1100, window.innerWidth - 32)}
        title={t("design.levers.title", "DOE 因子 · Levers")} destroyOnHidden>
        <LeversPanel />
      </Drawer>
    </PageContainer>
  );
}
