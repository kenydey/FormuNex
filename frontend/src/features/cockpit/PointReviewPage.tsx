import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Button, Input, InputNumber, Select, Space, Table } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";

interface DoeRun {
  run_id: number;
  natural?: Record<string, number>;
  review?: { decision: string; note: string; modified_factors: Record<string, number> | null };
}
interface PointsOut {
  plan_id: string;
  status: string;
  runs: DoeRun[];
  summary: { counts: Record<string, number>; total: number; effective_runs: { run_id: number; factors: Record<string, number> }[] };
}
interface Rules {
  ucb_kappa: number;
  early_stop_patience: number;
  early_stop_min_delta: number;
}

type Decision = "accept" | "modify" | "reject";
/** P1-18: decision 可空——只填备注的行不产生决策；提交时空决策行不发送（后端对缺 decision 的行 422）。 */
interface Edit {
  decision?: Decision;
  note: string;
  factors: Record<string, number>;
}

/** 补齐 2-17: 逐点审核（采纳/修改/驳回）+ 寻优规则微调。 */
export default function PointReviewPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const projectId = useUi((s) => s.projectId);
  const [planId, setPlanId] = useState<string | undefined>();
  const plans = useQuery({ queryKey: ["doe", "history", projectId], queryFn: () => call<{ items: { plan_id: string; design: string; created_at: string }[] }>("GET", `/api/doe/history?page_size=50${projectId ? `&project_id=${projectId}` : ""}`) });
  const points = useQuery({ queryKey: ["cockpit", "doe-points", planId], enabled: !!planId, queryFn: () => call<PointsOut>("GET", `/api/cockpit/doe/${planId}/points`) });
  const [edits, setEdits] = useState<Record<number, Edit>>({});
  const [rules, setRules] = useState<Rules>({ ucb_kappa: 1.5, early_stop_patience: 5, early_stop_min_delta: 1e-4 });
  const rulesQuery = useQuery({ queryKey: ["cockpit", "optimizer-rules"], queryFn: () => call<Rules>("GET", "/api/cockpit/optimizer-rules") });

  useEffect(() => { if (rulesQuery.data) setRules(rulesQuery.data); }, [rulesQuery.data]);
  useEffect(() => {
    if (!points.data) return;
    const next: Record<number, Edit> = {};
    for (const r of points.data.runs) {
      if (r.review) {
        next[r.run_id] = {
          decision: r.review.decision as Decision,
          note: r.review.note ?? "",
          factors: { ...(r.natural ?? {}), ...(r.review.modified_factors ?? {}) },
        };
      }
    }
    setEdits(next);
  }, [points.data]);

  const submit = useMutation({
    mutationFn: () => call("POST", `/api/cockpit/doe/${planId}/points/review`, {
      // P1-18: 只发送已显式决策的行——只填备注的行不生成 edit，绝不静默记为 accept。
      reviews: Object.entries(edits).filter(([, e]) => e.decision != null).map(([runId, e]) => ({
        run_id: Number(runId), decision: e.decision, note: e.note,
        factors: e.decision === "modify" ? e.factors : undefined,
      })),
    }),
    onSuccess: () => points.refetch(),
  });
  const saveRules = useMutation({
    mutationFn: () => call<Rules>("POST", "/api/cockpit/optimizer-rules", rules),
    onSuccess: (d) => setRules(d),
  });

  const runs = points.data?.runs ?? [];
  const factorNames = Array.from(new Set(runs.flatMap((r) => Object.keys(r.natural ?? {}))));

  return (
    <PageContainer subTitle={t("review.sub", "逐点采纳 / 修改因子 / 驳回；驳回的点保留记录但不进入有效点")}>
      <ProCard bordered>
        <Space wrap>
          <Select allowClear style={{ width: 360 }} placeholder={t("review.plan", "DOE 计划")} value={planId} onChange={setPlanId}
            options={(plans.data?.items ?? []).map((p) => ({ value: p.plan_id, label: `${p.design} · ${p.created_at?.slice(0, 10)} · ${p.plan_id.slice(0, 8)}` }))} />
          {points.data && <span>{t("review.summary", "待审核 {{pending}} · 采纳 {{accept}} · 修改 {{modify}} · 驳回 {{reject}}，有效 {{effective}} 点", {
            pending: points.data.summary.counts.pending ?? 0, accept: points.data.summary.counts.accept ?? 0,
            modify: points.data.summary.counts.modify ?? 0, reject: points.data.summary.counts.reject ?? 0,
            effective: points.data.summary.effective_runs.length,
          })}</span>}
        </Space>
      </ProCard>
      <ErrorAlert error={points.error ?? submit.error} />
      {points.data && (
        <ProCard bordered style={{ marginTop: 16 }}>
          <Table size="small" rowKey="run_id" pagination={false} dataSource={runs} columns={[
            { title: t("review.run", "Run"), dataIndex: "run_id" },
            { title: t("review.factors", "因子（natural）"), render: (_v: unknown, r: DoeRun) => JSON.stringify(r.natural ?? {}) },
            { title: t("review.decision", "审核"), render: (_v: unknown, r: DoeRun) => (
              <Select style={{ width: 120 }} allowClear placeholder={t("review.pending", "待审核")}
                value={edits[r.run_id]?.decision}
                options={[{ value: "accept", label: t("review.accept", "采纳") }, { value: "modify", label: t("review.modify", "修改") }, { value: "reject", label: t("review.reject", "驳回") }]}
                onChange={(v) => setEdits((e) => ({ ...e, [r.run_id]: { decision: v as Decision | undefined, note: e[r.run_id]?.note ?? "", factors: e[r.run_id]?.factors ?? { ...(r.natural ?? {}) } } }))} />
            ) },
            { title: t("review.modify_factors", "修改后因子（仅修改时）"), render: (_v: unknown, r: DoeRun) => {
              const e = edits[r.run_id];
              if (!e || e.decision !== "modify") return "—";
              return <Space wrap>{factorNames.map((f) => (
                <span key={f}>{f}: <InputNumber size="small" style={{ width: 90 }} value={e.factors[f]}
                  onChange={(v) => setEdits((prev) => ({ ...prev, [r.run_id]: { ...e, factors: { ...e.factors, [f]: v ?? 0 } } }))} /></span>
              ))}</Space>;
            } },
            { title: t("review.note", "备注"), render: (_v: unknown, r: DoeRun) => (
              <Input style={{ width: 160 }} value={edits[r.run_id]?.note ?? ""}
                // P1-18: 备注输入只写备注，不写 decision——待审核行上只敲备注不再产生一条 accept 编辑；
                // 备注随已选决策一起提交，未决策时备注仅本地留存、提交时不发送。
                onChange={(e) => setEdits((prev) => { const cur = prev[r.run_id]; if (!cur) return { ...prev, [r.run_id]: { decision: undefined, note: e.target.value, factors: { ...(r.natural ?? {}) } } }; return { ...prev, [r.run_id]: { ...cur, note: e.target.value } }; })} />
            ) },
          ]} />
          <Button type="primary" style={{ marginTop: 12 }} disabled={!can("doe.write")} loading={submit.isPending} onClick={() => submit.mutate()}>{t("review.submit", "提交审核")}</Button>
        </ProCard>
      )}
      <ProCard bordered style={{ marginTop: 16 }} title={t("review.rules_title", "寻优规则微调（仅 numpy-UCB 引擎与早停读取，立即生效）")}>
        <Space wrap>
          <span>{t("review.kappa", "UCB κ")}</span>
          <InputNumber min={0.01} max={10} step={0.1} value={rules.ucb_kappa} onChange={(v) => setRules((r) => ({ ...r, ucb_kappa: v ?? 1.5 }))} />
          <span>{t("review.patience", "早停 patience")}</span>
          <InputNumber min={1} max={100} step={1} value={rules.early_stop_patience} onChange={(v) => setRules((r) => ({ ...r, early_stop_patience: v ?? 5 }))} />
          <span>{t("review.min_delta", "早停 min_delta")}</span>
          <InputNumber min={0.000001} max={1} step={0.0001} value={rules.early_stop_min_delta} onChange={(v) => setRules((r) => ({ ...r, early_stop_min_delta: v ?? 1e-4 }))} />
          <Button type="primary" disabled={!can("admin.llm")} loading={saveRules.isPending} onClick={() => saveRules.mutate()}>{t("review.rules_save", "保存规则")}</Button>
        </Space>
        <ErrorAlert error={saveRules.error} />
      </ProCard>
    </PageContainer>
  );
}
