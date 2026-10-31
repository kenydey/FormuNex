import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Empty, InputNumber, Progress, Select, Space, Switch, Table, Tag, Tooltip, Typography } from "antd";
import { DownloadOutlined, PauseCircleOutlined, PlayCircleOutlined, SyncOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { call, download } from "@/api/client";
import { useSession } from "@/app/session";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import { HandoffBanner } from "@/shared/handoff";
import { coldStartMessage, useCampaigns, useEngines, useProjectWorkspace } from "./hooks";
import { adoptPlan, cancelLoop, runLoop, useLoopRun } from "./loop";
import { AdaptiveDoeInsights, TrainingDataBanner } from "./panels";
import type { DoeRun } from "./types";

interface CycleStatus { isPaused: boolean; lastUpdated: string | null; campaignId: number; pausedUntil?: string | null; lapsedAt?: string | null; degraded?: boolean }
interface CycleRuns { summary: { cycle_count: number; total_experiments: number; measured_count: number; last_engine: string; last_status: string } }

const stamp = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

/** 自驱动闭环 (legacy ``LoopModal``): data → retrain → optimise → next active-learning DOE batch. */
export default function LoopPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const { modal } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const { projectId, ws, patch, project } = useProjectWorkspace();
  const campaigns = useCampaigns();
  const engines = useEngines();
  const run = useLoopRun();
  const baybe = engines.ok("baybe");
  const campaignId = search.get("campaign") ? Number(search.get("campaign")) : (ws.workbench_campaign_id ?? null);
  const [picked, setPicked] = useState<number | null>(null);
  const cid = picked ?? campaignId;

  const report = ws.loop_report ?? run.lastReport;
  const converged = Boolean(report?.converged);
  const plan = ws.doe_plan ?? report?.next_doe ?? null;
  const pendingAdopt = !!plan && (!plan.plan_id || plan.plan_id !== ws.workbench_adopted_plan_id);

  const envFlags = useQuery({ queryKey: ["settings", "env-flags"], retry: false, staleTime: 60_000,
    queryFn: () => call<{ flags: { attr: string; value: unknown }[] }>("GET", "/api/settings/env-flags") });
  const envAutoAdopt = envFlags.data?.flags?.find((f) => f.attr === "auto_adopt_next_doe_on_loop")?.value;

  const status = useQuery({ queryKey: ["workbench", cid, "cycle-status"], enabled: cid != null, refetchInterval: 5000, retry: false,
    queryFn: () => call<CycleStatus>("GET", `/api/experiments/hooks/doecycle-status/${cid}`) });
  const stats = useQuery({ queryKey: ["doe", "cycle-runs", projectId], enabled: !!projectId, retry: false,
    queryFn: () => call<CycleRuns>("GET", `/api/doe/cycle-runs?project_id=${encodeURIComponent(projectId!)}`) });
  const pause = useMutation({
    mutationFn: () => call("POST", `/api/experiments/hooks/pause-doecycle/${cid}`, { isPaused: !status.data?.isPaused }),
    onSuccess: () => void status.refetch(),
  });
  const adopt = useMutation({
    mutationFn: () => adoptPlan(qc, projectId, plan!, { engineHint: report?.engine }),
    onSuccess: ({ campaign }) => {
      navigate(`/cockpit/workbench/${campaign.campaign_id}`);
    },
  });

  const confirmFlag = (key: "auto_adopt_next_doe_on_loop" | "auto_loop_on_sync", on: boolean, text: string) => {
    const apply = () => patch.mutate({ [key]: on, ...(key === "auto_loop_on_sync" ? { auto_loop_round: 0 } : {}) });
    if (on) modal.confirm({ title: t("workbench.flag_confirm", "确认开启？"), content: <div style={{ whiteSpace: "pre-line" }}>{text}</div>, onOk: apply });
    else apply();
  };

  const ev = run.event;
  const rmseHistory = ws.rmse_history ?? [];
  const lastRmse = (metric: string) => {
    const s = rmseHistory.map((h) => h[metric]).filter((v) => v != null);
    return { last: s[s.length - 1], improving: s.length > 1 && s[s.length - 1] < s[0] };
  };
  const metricsInHistory = Array.from(new Set(rmseHistory.flatMap((h) => Object.keys(h))));
  const paused = !!status.data?.isPaused;

  if (!projectId) {
    return <PageContainer><HandoffBanner /><Alert type="info" showIcon message={t("workbench.loop_need_project", "闭环迭代基于项目的技术需求：请先在顶部选择项目")} /></PageContainer>;
  }

  return (
    <PageContainer subTitle={t("workbench.loop_sub", "读取已录入实验数据 → 用最新模型寻优 → 生成下一批主动学习 DOE，形成 实验→数据→优化→新实验 的闭环")}>
      <HandoffBanner />
      <TrainingDataBanner />
      <ErrorAlert error={project.error ?? patch.error ?? pause.error ?? adopt.error} />
      <ProCard bordered>
        <Space wrap>
          <span>{t("workbench.campaign", "实验台账")}</span>
          <Select style={{ width: 300 }} allowClear placeholder={t("workbench.campaign_none", "不绑定台账（仅用训练库）")} value={cid ?? undefined}
            onChange={(v) => { setPicked(v ?? null); patch.mutate({ workbench_campaign_id: v ?? null }); }}
            options={campaigns.items.map((c) => ({ value: c.id, label: `#${c.id} · ${c.name} · ${c.row_count}` }))} />
          <Select style={{ width: 170 }} value={ws.optimize_engine || "auto"} onChange={(v) => patch.mutate({ optimize_engine: v })} data-testid="loop-optimize-engine"
            options={[{ value: "auto", label: t("workbench.opt.auto", "寻优：自动") }, { value: "legacy", label: t("workbench.opt.legacy", "寻优：经典链") },
              { value: "baybe", label: baybe ? t("workbench.opt.baybe", "寻优：BayBE") : t("workbench.opt.baybe_na", "寻优：BayBE（未安装）"), disabled: !baybe }]} />
          <Select style={{ width: 170 }} value={ws.loop_doe_engine || "auto"} onChange={(v) => patch.mutate({ loop_doe_engine: v })} data-testid="loop-doe-engine"
            options={[{ value: "auto", label: t("workbench.doe.auto", "DOE：自动") }, { value: "legacy", label: t("workbench.doe.legacy", "DOE：经典 EI") },
              { value: "baybe", label: baybe ? t("workbench.doe.baybe", "DOE：BayBE") : t("workbench.doe.baybe_na", "DOE：BayBE（未安装）"), disabled: !baybe }]} />
          {(ws.optimize_engine === "baybe" || ws.loop_doe_engine === "baybe") && !baybe && <Tag color="warning">{t("workbench.baybe_missing", "已选 BayBE 但未安装")}</Tag>}
        </Space>
        <Space wrap style={{ marginTop: 12 }}>
          <Tooltip title={t("workbench.auto_adopt_tip", "闭环成功后自动把 next_doe 写入台账（默认关）")}>
            <Space><Switch checked={!!ws.auto_adopt_next_doe_on_loop} data-testid="loop-auto-adopt"
              onChange={(on) => confirmFlag("auto_adopt_next_doe_on_loop", on, t("workbench.auto_adopt_confirm", "闭环成功后会把 next_doe 写入实验台账（不可静默撤销）。\n建议仅在已审 campaign 上开启；与「自主」闭环独立。"))} />
              {t("workbench.auto_adopt", "自动采纳 DOE")}</Space>
          </Tooltip>
          {ws.auto_adopt_next_doe_on_loop && <Tag color="warning">{t("workbench.will_write", "将写入台账")}</Tag>}
          {typeof envAutoAdopt === "boolean" && <Tag>{t("workbench.env_default", "全局默认：{{v}}", { v: envAutoAdopt ? "on" : "off" })}</Tag>}
          <Space><Switch checked={!!ws.auto_loop_on_sync}
            onChange={(on) => confirmFlag("auto_loop_on_sync", on, t("workbench.autonomy_confirm", "台账保存 Completed 后将自动触发 optimize + 下一轮 DOE，未收敛时按上限自动继续。\n默认仍不自动采纳 DOE。"))} />
            {t("workbench.autonomy", "自主闭环")}</Space>
          {ws.auto_loop_on_sync && (
            <Space>{t("workbench.max_rounds", "最多")}<InputNumber size="small" min={1} max={10} value={ws.auto_loop_max_rounds ?? 5}
              onChange={(v) => patch.mutate({ auto_loop_max_rounds: Math.max(1, Math.min(10, Number(v ?? 5))) })} />
              {t("workbench.rounds_unit", "轮")}{run.running ? ` (${(ws.auto_loop_round ?? 0) + 1}/${ws.auto_loop_max_rounds ?? 5})` : ""}</Space>
          )}
        </Space>
        <Space wrap style={{ marginTop: 12 }}>
          <Button type="primary" icon={<SyncOutlined spin={run.running} />} disabled={run.running || converged || !can("optimize.run")} loading={run.running}
            onClick={() => void runLoop(qc, projectId, cid)} title={converged ? t("workbench.converged_tip", "模型 RMSE 已收敛，建议停止迭代") : undefined}>
            {run.running ? coldStartMessage(ev?.stage, ev?.message, t("workbench.iterating", "迭代中…"), t("workbench.cold", "模型冷启动中… 正在检索"))
              : converged ? t("workbench.loop.converged", "已收敛") : t("workbench.run_loop", "迭代一轮闭环")}
          </Button>
          {run.running && <Button danger onClick={() => void cancelLoop()}>{t("workbench.cancel", "取消")}</Button>}
          <Button data-testid="doe-cycle-pause" icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />} disabled={cid == null || run.running}
            loading={pause.isPending} onClick={() => pause.mutate()}
            title={cid == null ? t("workbench.pause_need", "请先选择实验台账") : undefined}>
            {paused ? t("workbench.resume", "恢复闭环") : t("workbench.pause", "暂停闭环")}
          </Button>
          <span data-testid="doe-cycle-status">
            {cid == null ? <Tag>{t("workbench.cycle.unbound", "DOE 闭环：未绑定台账")}</Tag>
              : paused ? <Tag color="warning">{t("workbench.cycle.paused", "DOE 闭环：已暂停")}{" · "}
                {status.data?.pausedUntil ? t("workbench.cycle.until", "至 {{t}} 自动恢复", { t: stamp(status.data.pausedUntil) }) : t("workbench.cycle.forever", "恢复前一直暂停")}</Tag>
                : <Tag color="success">{t("workbench.cycle.running", "DOE 闭环：运行中")}</Tag>}
            {cid != null && !paused && status.data?.lapsedAt && <Tag color="warning">{t("workbench.cycle.lapsed", "暂停已于 {{t}} 到期，已自动恢复", { t: stamp(status.data.lapsedAt) })}</Tag>}
          </span>
        </Space>
        {status.error && <Typography.Text type="danger" style={{ display: "block", marginTop: 8 }}>{String((status.error as Error).message)}</Typography.Text>}
        {run.running && ev && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">{[ev.stage, ev.message, ev.elapsed_ms != null ? `${(ev.elapsed_ms / 1000).toFixed(1)}s` : ""].filter(Boolean).join(" · ")}</Typography.Text>
            {ev.progress != null && ev.progress > 0 && <Progress percent={Math.round(ev.progress * 100)} size="small" />}
          </div>
        )}
      </ProCard>

      {run.retry && !run.running && (
        <Alert type="error" showIcon style={{ marginTop: 16 }} data-testid="loop-retry-banner"
          message={t("workbench.loop_failed", "闭环失败：{{e}}", { e: run.error ?? "" })}
          action={<Button size="small" danger disabled={!can("optimize.run")} onClick={() => void runLoop(qc, projectId, cid)}>{t("workbench.loop_retry", "重试闭环")}</Button>} />
      )}
      {!run.retry && run.error && !run.running && <Alert type="warning" showIcon style={{ marginTop: 16 }} message={run.error} />}

      {stats.data && (
        <ProCard ghost gutter={16} style={{ marginTop: 16 }} wrap data-testid="doe-cycle-stats">
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.cycles", "闭环轮数"), value: stats.data.summary.cycle_count }} />
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.suggested", "累计建议实验"), value: stats.data.summary.total_experiments }} />
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.measured", "已测量"), value: stats.data.summary.measured_count }} />
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.engine", "上次引擎"), value: stats.data.summary.last_engine || "—" }} />
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.status", "上次状态"), value: stats.data.summary.last_status || "—",
            status: stats.data.summary.last_status === "success" ? "success" : "error" }} />
          <StatisticCard colSpan={{ xs: 12, md: 4 }} statistic={{ title: t("workbench.stat.converged", "收敛"), value: converged ? t("workbench.loop.converged", "已收敛") : t("workbench.not_converged", "未收敛") }} />
        </ProCard>
      )}

      {!report && !run.running && <Empty style={{ marginTop: 32 }} description={t("workbench.loop_empty", "点击「迭代一轮闭环」：寻优结果刷新配方排行，下一批 DOE 自动生成并标记最有信息量的实验点。")} />}

      {report && (
        <>
          {converged && <Alert type="warning" showIcon style={{ marginTop: 16 }} message={report.loop_message || t("workbench.converged_msg", "模型 RMSE 已进入平台期，建议停止闭环迭代。")} />}
          {report.chemical_feasibility && report.chemical_feasibility.feasible === false && (
            <Alert type="error" showIcon style={{ marginTop: 16 }} message={t("workbench.kg_feasibility", "知识图谱化学可行性告警")}
              description={<ul style={{ margin: 0 }}>{report.chemical_feasibility.reasons.map((r) => <li key={r}>{r}</li>)}</ul>} />
          )}
          {report.physical_constraints && report.physical_constraints.status !== "pass" && (
            <Alert type={report.physical_constraints.status === "infeasible" ? "error" : "warning"} showIcon style={{ marginTop: 16 }}
              message={report.physical_constraints.status === "infeasible" ? t("workbench.phys_infeasible", "物理约束不可行（酸性稳定性 / 合规）") : t("workbench.phys_warn", "物理约束提示")}
              description={<ul style={{ margin: 0 }}>{report.physical_constraints.reasons.map((r) => <li key={r}>{r}</li>)}</ul>} />
          )}
          <ProCard bordered style={{ marginTop: 16 }} title={t("workbench.report", "本轮结果")}>
            <Descriptions size="small" column={{ xs: 1, md: 3 }} items={[
              { key: "engine", label: t("workbench.engine", "引擎"), children: <Tag color="blue">{report.engine}</Tag> },
              { key: "records", label: t("workbench.records", "已录入实验"), children: report.total_records },
              { key: "models", label: t("workbench.models_n", "本域模型"), children: report.model_info.length },
              ...(report.optimization?.measurement_source ? [{ key: "src", label: t("workbench.meas_src", "寻优数据来源"),
                children: `${report.optimization.measurement_source}${report.optimization.lab_points_used ? ` · ${report.optimization.lab_points_used}` : ""}` }] : []),
              ...(report.cost_summary?.cost_cny_per_kg != null ? [{ key: "cost", label: t("workbench.cost", "均成本"), children: `${report.cost_summary.cost_cny_per_kg} CNY/kg` }] : []),
              ...(report.cost_summary?.voc_gpl != null ? [{ key: "voc", label: t("workbench.voc", "均 VOC"), children: `${report.cost_summary.voc_gpl} g/L` }] : []),
            ]} />
            {report.model_info.length ? (
              <ProCard ghost gutter={[12, 12]} wrap style={{ marginTop: 12 }}>
                {report.model_info.map((m) => {
                  const h = lastRmse(m.metric);
                  return (
                    <ProCard key={`${m.domain}-${m.metric}`} bordered size="small" colSpan={{ xs: 24, md: 12, xl: 8 }} title={<code>{m.metric}</code>}
                      extra={<Typography.Text type="secondary">{m.backend} · n={m.n_samples}</Typography.Text>}>
                      <Space size="large">
                        <span>R² = {m.r2.toFixed(2)}</span>
                        <Typography.Text type={h.improving ? "success" : undefined}>RMSE = {(h.last ?? m.rmse).toFixed((h.last ?? m.rmse) < 1 ? 3 : 1)}{h.improving ? " ↓" : ""}</Typography.Text>
                      </Space>
                    </ProCard>
                  );
                })}
              </ProCard>
            ) : <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
              {t("workbench.no_models", "暂无训练模型（实验样本不足）：当前使用经验先验 + LHS 选点；录入 ≥4 条实验后模型自动激活。")}</Typography.Paragraph>}
            <ProCard ghost gutter={16} wrap style={{ marginTop: 12 }}>
              {(report.optimization?.history?.length ?? 0) > 0 && (
                <ProCard colSpan={{ xs: 24, lg: 12 }} title={t("workbench.convergence", "寻优收敛")} size="small" bordered>
                  <Chart height={240} option={{ tooltip: { trigger: "axis" }, xAxis: { type: "category", data: report.optimization.history.map((_v, i) => i + 1) },
                    yAxis: { type: "value", scale: true }, series: [{ type: "line", smooth: true, data: report.optimization.history }] }} />
                </ProCard>
              )}
              {rmseHistory.length > 0 && (
                <ProCard colSpan={{ xs: 24, lg: 12 }} title={t("workbench.rmse_trend", "各轮 RMSE")} size="small" bordered>
                  <Chart height={240} option={{ tooltip: { trigger: "axis" }, legend: { data: metricsInHistory },
                    xAxis: { type: "category", data: rmseHistory.map((_h, i) => `R${i + 1}`) }, yAxis: { type: "value", scale: true },
                    series: metricsInHistory.map((m) => ({ name: m, type: "line" as const, data: rmseHistory.map((h) => h[m] ?? null) })) }} />
                </ProCard>
              )}
            </ProCard>
          </ProCard>
        </>
      )}

      {plan && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("workbench.next_doe", "下一批推荐实验 · Next DOE")}
          subTitle={<Tag color="purple">{t("workbench.ai_point", "AI = 主动学习选点")}</Tag>}
          extra={<Space>
            {pendingAdopt && <Button type="primary" disabled={!can("lab.execute", projectId)} loading={adopt.isPending} onClick={() => adopt.mutate()}>{t("workbench.adopt", "创建实验台账 →")}</Button>}
            {!pendingAdopt && ws.workbench_campaign_id != null && <Link to={`/cockpit/workbench/${ws.workbench_campaign_id}`}>{t("workbench.open_ledger", "打开台账")}</Link>}
            {plan.plan_id && <Button icon={<DownloadOutlined />} onClick={() => void download(`/api/doe/${plan.plan_id}/export?format=csv`, `doe_${plan.plan_id}.csv`)}>
              {t("workbench.export_next", "导出下一批 DOE")}</Button>}
          </Space>}>
          <AdaptiveDoeInsights meta={ws.adaptive_doe ?? report} plan={plan} />
          <Table<DoeRun> size="small" rowKey="run_id" pagination={false} scroll={{ x: "max-content", y: 360 }} dataSource={plan.runs}
            columns={[
              { title: "#", dataIndex: "run_id", width: 80, fixed: "left", render: (v: number, r) => <Space size={4}>{v}{r.ai_suggested && <Tag color="purple">AI</Tag>}
                {r.infeasible && <Tooltip title={r.infeasible_reason}><Tag color="error">!</Tag></Tooltip>}</Space> },
              ...plan.factors.map((f) => ({ title: `${f.name}${f.unit ? ` (${f.unit})` : ""}`, key: f.name, render: (_v: unknown, r: DoeRun) => String(r.natural[f.name] ?? "—") })),
            ]} />
        </ProCard>
      )}
    </PageContainer>
  );
}
