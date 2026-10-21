import { ModalForm, PageContainer, ProFormTextArea } from "@ant-design/pro-components";
import { TechReportExport } from "@/features/research/TechReportExport";
import { Alert, App, Button, Dropdown, Empty, Segmented, Space, Table, Tag, Typography } from "antd";
import { DownloadOutlined, PlusOutlined, RobotOutlined, SyncOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { EChartsOption } from "echarts";
import { Chart } from "@/shared/Chart";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { fx, saveText } from "./api";
import { useOpenInEditor } from "./adopt";
import { FormulaCard } from "./FormulaCard";
import { RelationInsightsBanner } from "./RelationInsights";
import { leaderboardToCsv, OBJECTIVE_METRIC, paretoFront, shiftAfterRemove, warningActions } from "./logic";
import type { Formulation, Ingredient } from "./types";
import { useResearchTask } from "./useTask";
import { groundingSources, useWorkspace } from "./workspace";

type View = "cards" | "table" | "pareto" | "parallel";

/** Top recommended formulations of the current project (legacy FormulaLeaderboard). */
export default function LeaderboardPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view = (["cards", "table", "pareto", "parallel"].includes(params.get("view") ?? "") ? params.get("view") : "cards") as View;
  const { projectId, project, ws, leaderboard, requirement, recommendId, patch } = useWorkspace();
  const openInEditor = useOpenInEditor();
  const task = useResearchTask();
  const [draft, setDraft] = useState<Formulation[]>(leaderboard);
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [aiTarget, setAiTarget] = useState<number | null>(null);
  const [saveIdx, setSaveIdx] = useState<number | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  useEffect(() => { if (editIdx == null) setDraft(leaderboard); }, [leaderboard, editIdx]);

  const validate = useQuery({
    queryKey: ["formulation", "validate", projectId, JSON.stringify(leaderboard)], enabled: leaderboard.length > 0, staleTime: 60_000,
    queryFn: () => fx.validate(leaderboard, requirement),
  });
  const warnings = validate.data?.warnings ?? [];
  const actions = warningActions(warnings);
  const objectiveMetrics = useMemo(() => new Set((requirement?.objectives ?? []).map((o) => o.metric)), [requirement]);
  const base = requirement?.active_formulation;

  const persist = (forms: Formulation[]) => patch.mutateAsync({ leaderboard: forms }).catch((e) => { message.error(errorText(e)); throw e; });

  const saveDoe = async (idx: number) => {
    const form = draft[idx];
    setSaveIdx(idx);
    try {
      const hits = await fx.findLineages(form.name, form.domain, 1);
      const lin = hits[0];
      await fx.saveVersion({ formulation: form, lineage_id: lin?.lineage_id ?? null, parent_version_id: lin?.versions.at(-1)?.id ?? null,
        change_summary: t("formulation.board.doe_summary", "从推荐列表保存为 DOE 基准配方"), created_by: "ui", project_id: projectId });
      const uid = form.client_uid ?? crypto.randomUUID();
      const forms = draft.map((f, i) => (i === idx ? { ...f, client_uid: uid } : f));
      await patch.mutateAsync({ leaderboard: forms, requirement: { ...requirement!, active_formulation: { ...form, client_uid: uid } } });
      message.success(t("formulation.board.doe_saved", "已保存并设为 DOE 基准"));
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaveIdx(null);
    }
  };
  const remove = (idx: number) => {
    const forms = draft.filter((_, i) => i !== idx);
    setEditIdx((c) => shiftAfterRemove(c, idx));
    setFocus((c) => shiftAfterRemove(c, idx));
    setDraft(forms);
    void persist(forms);
  };
  const changeIng = (fi: number, ii: number, p: Partial<Ingredient>) =>
    setDraft((d) => d.map((f, i) => (i === fi ? { ...f, ingredients: f.ingredients.map((g, j) => (j === ii ? { ...g, ...p } : g)) } : f)));
  const toggleEdit = (idx: number) => {
    if (editIdx === idx) { void persist(draft).then(() => setEditIdx(null)); } else setEditIdx(idx);
  };
  const addManual = useMutation({
    mutationFn: () => fx.manual({ name: t("formulation.board.manual_name", "手动配方"), domain: requirement?.domain ?? "anticorrosion_coating",
      ingredients: [{ name: "", zh_name: "", role: "additive", weight_pct: 0, cas_no: "" }], rationale: "manual", predicted: {}, predicted_std: {},
      score: null, warnings: [], source: "manual" }, requirement),
    onSuccess: async (r) => { const forms = [...draft, r.formulation]; await persist(forms); setEditIdx(forms.length - 1); setFocus(forms.length - 1); },
    onError: (e) => message.error(errorText(e)),
  });
  const proposeMissing = useMutation({
    mutationFn: async () => {
      const materials = draft.flatMap((f) => f.ingredients.map((g) => ({ name: g.name, role: g.role || "", cas_no: g.cas_no || undefined, smiles: g.smiles || undefined })));
      if (!materials.length) throw new Error(t("formulation.board.no_ing", "当前配方无组分可入库"));
      const r = await fx.proposeMaterials(materials);
      const before = warnings.length;
      const v = await fx.validate(draft, requirement);
      await persist(v.formulations ?? draft);
      return t("formulation.board.proposed", "组分入库：自动 {{u}} · 待确认 {{p}} · 已存在 {{e}} · 重校验 {{a}}→{{b}} 条",
        { u: r.upsert ?? 0, p: r.pending ?? 0, e: r.exists ?? 0, a: before, b: v.warnings?.length ?? 0 });
    },
    onSuccess: setSyncMsg,
    onError: (e) => setSyncMsg(t("formulation.board.propose_failed", "入库失败：{{e}}", { e: errorText(e) })),
  });
  const aiModify = async (prompt: string) => {
    const target = aiTarget ?? 0;
    try {
      const research = await task.run("/api/research/modify", {
        requirement: { ...requirement, project_id: projectId }, modify_prompt: prompt, sources: groundingSources(ws), base_formulas: draft,
        base_formulation: draft[target], query: ws?.search_query ?? "", n: 3,
      });
      if (!research.recommended?.length) throw new Error(t("formulation.board.ai_empty", "AI 修改未返回配方"));
      let forms = [...draft, ...research.recommended.map((f) => ({ ...f, source: "ai_modify" }))];
      try { forms = (await fx.validate(forms, requirement)).formulations ?? forms; } catch { /* best-effort */ }
      await patch.mutateAsync({ leaderboard: forms, research: { ...research, recommended: forms, chat_markdown: `AI 修改：${prompt}` } });
      setAiTarget(null);
      return true;
    } catch (e) {
      message.error(errorText(e));
      return false;
    }
  };
  const showCard = (idx: number) => { setFocus(idx); setParams((prev) => { const next = new URLSearchParams(prev); next.delete("view"); return next; }); };

  if (!projectId) return <PageContainer><Alert type="info" showIcon message={t("formulation.need_project", "请先在顶部选择项目")} /></PageContainer>;
  return (
    <PageContainer loading={project.isLoading}
      title={t("formulation.board.title", "Top 推荐配方")}
      tags={ws?.research?.recommend_engine ? <Tag color={ws.research.recommend_engine === "llm" ? "blue" : "default"}>
        {ws.research.recommend_engine === "llm" ? t("formulation.rec.llm", "LLM 推荐") : t("formulation.rec.offline", "离线模板")}</Tag> : undefined}
      extra={<Space wrap>
        <TechReportExport kind="formulation" projectId={projectId} />
        <Segmented value={view} onChange={(v) => setParams((prev) => { const next = new URLSearchParams(prev); if (v === "cards") next.delete("view"); else next.set("view", String(v)); return next; })} options={[
          { value: "cards", label: t("formulation.board.cards", "卡片") }, { value: "table", label: t("formulation.board.table", "列表") },
          { value: "pareto", label: t("formulation.board.pareto", "帕累托") }, { value: "parallel", label: t("formulation.board.parallel", "平行坐标") }]} />
        <Dropdown disabled={!draft.length} menu={{ items: [
          { key: "csv", label: t("formulation.card.csv", "导出 CSV"), onClick: () => saveText(leaderboardToCsv(draft), `leaderboard_${draft.length}.csv`) },
          { key: "json", label: t("formulation.board.json", "导出 JSON"), onClick: () => saveText(JSON.stringify(draft, null, 2), `leaderboard_${draft.length}.json`, "application/json") },
        ] }}><Button icon={<DownloadOutlined />}>{t("formulation.board.export", "导出列表")}</Button></Dropdown>
      </Space>}>
      <ErrorAlert error={project.error} />
      <RelationInsightsBanner insights={ws?.research?.relation_insights} />
      {warnings.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("formulation.board.validate", "配方校验 / 目录补全提示")}
        description={<>
          <ul style={{ margin: 0, paddingInlineStart: 18, maxHeight: 140, overflow: "auto" }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          {actions.hasCompliance && <Typography.Paragraph type="secondary" style={{ margin: "6px 0 0" }}>{t("formulation.board.compliance", "合规项（REACH / SVHC / RoHS）需人工确认，不会自动改配方。")}</Typography.Paragraph>}
          <Space style={{ marginTop: 8 }} wrap>
            {actions.proposeMissing && <Button size="small" loading={proposeMissing.isPending} onClick={() => proposeMissing.mutate()}>{t("formulation.board.propose", "入库缺失组分")}</Button>}
            {actions.openMaterials && <Link to="/materials">{t("formulation.board.materials", "打开材料库")}</Link>}
            {syncMsg && <Typography.Text type="secondary">{syncMsg}</Typography.Text>}
          </Space>
        </>} />}
      {draft.length === 0 ? (
        <Empty description={t("formulation.board.empty", "尚无配方。先运行检索推荐或寻优。")}>
          <Space><Button type="primary" onClick={() => navigate("/lab/recommend")}>{t("formulation.board.go_rec", "去推荐")}</Button></Space>
        </Empty>
      ) : view === "table" ? <BoardTable forms={draft} onSelect={showCard} />
        : view === "pareto" ? <Pareto forms={draft} onSelect={showCard} />
          : view === "parallel" ? <Parallel forms={draft} />
            : draft.map((f, i) => (
              <FormulaCard key={`${f.name}-${i}-${focus === i ? "f" : ""}`} form={f} rank={i + 1} index={i} defaultOpen={i === 0 || focus === i}
                warnings={warnings} objectiveMetrics={objectiveMetrics} recommendId={recommendId} projectId={projectId} levers={requirement?.levers}
                editing={editIdx === i} onToggleEdit={() => toggleEdit(i)} onIngredientChange={(ii, p) => changeIng(i, ii, p)}
                isDoeBase={base ? (base.client_uid ? f.client_uid === base.client_uid : f.name === base.name) : false}
                saveBusy={saveIdx === i} onSaveDoe={() => void saveDoe(i)} onRemove={() => remove(i)} onAiModify={() => setAiTarget(i)}
                onOpenEditor={() => openInEditor(f, { recommendId, index: i, projectId, levers: requirement?.levers })} />
            ))}
      <Space style={{ marginTop: 12 }} wrap>
        <Button icon={<PlusOutlined />} loading={addManual.isPending} onClick={() => addManual.mutate()}>{t("formulation.board.add_manual", "手动添加配方")}</Button>
        <Button icon={<RobotOutlined />} disabled={!draft.length || task.busy} onClick={() => setAiTarget(0)}>{t("formulation.board.ai", "AI 修改配方")}</Button>
        <Button icon={<SyncOutlined />} disabled={!draft.length} loading={proposeMissing.isPending} onClick={() => proposeMissing.mutate()}
          title={t("formulation.board.propose_tip", "将推荐配方中的组分提交到全局材料库（有 CAS / SMILES 自动入库，否则进待确认）")}>{t("formulation.board.propose", "入库缺失组分")}</Button>
        {syncMsg && !warnings.length && <Typography.Text type="secondary">{syncMsg}</Typography.Text>}
      </Space>
      <ModalForm<{ prompt: string }> open={aiTarget != null} width={520} modalProps={{ destroyOnHidden: true, onCancel: () => setAiTarget(null), okButtonProps: { loading: task.busy } }}
        title={aiTarget != null ? t("formulation.board.ai_title", "AI 修改 · 基准 #{{n}} {{name}}", { n: aiTarget + 1, name: draft[aiTarget]?.name ?? "" }) : ""}
        submitter={{ searchConfig: { submitText: t("formulation.board.ai_submit", "提交修改") } }}
        onFinish={(v) => aiModify(v.prompt.trim())}>
        <Typography.Paragraph type="secondary">{t("formulation.board.ai_hint", "将仅以该配方为基准生成修改变体（原配方保留，变体追加为新卡片）。")}</Typography.Paragraph>
        <ProFormTextArea name="prompt" rules={[{ required: true, whitespace: true }]} fieldProps={{ rows: 4 }}
          placeholder={t("formulation.board.ai_ph", "例：将 VOC 降至 250 g/L 以下，用硅烷偶联剂替代部分环氧")} />
        {task.busy && <Typography.Text type="secondary">{task.event?.message || t("formulation.board.ai_busy", "AI 修改配方中…")}</Typography.Text>}
      </ModalForm>
    </PageContainer>
  );
}

function BoardTable({ forms, onSelect }: { forms: Formulation[]; onSelect: (i: number) => void }) {
  const { t } = useTranslation();
  const metrics = Array.from(new Set(forms.flatMap((f) => Object.keys(f.predicted ?? {})))).slice(0, 8);
  return (
    <Table size="small" rowKey="i" pagination={false} scroll={{ x: true }} dataSource={forms.map((f, i) => ({ f, i }))}
      onRow={({ i }) => ({ onClick: () => onSelect(i), style: { cursor: "pointer" } })}
      columns={[
        { title: "#", width: 50, render: (_d, { i }) => i + 1 },
        { title: t("formulation.formula_name", "配方名称"), render: (_d, { f }) => f.name },
        { title: "Score", width: 80, render: (_d, { f }) => f.score?.toFixed(2) ?? "—" },
        ...metrics.map((m) => ({ title: m, key: m, render: (_d: unknown, { f }: { f: Formulation }) => f.predicted?.[m] ?? "—" })),
        { title: t("formulation.board.ing_n", "组分数"), width: 80, render: (_d, { f }) => f.ingredients.length },
      ]} />
  );
}

function Pareto({ forms, onSelect }: { forms: Formulation[]; onSelect: (i: number) => void }) {
  const { t } = useTranslation();
  const y = OBJECTIVE_METRIC[forms[0]?.domain] ?? "salt_spray_hours";
  const pts = forms.map((f, i) => ({ i, name: f.name, x: f.predicted?.cost_cny_per_kg, y: f.predicted?.[y] })).filter((p) => p.x != null && p.y != null) as { i: number; name: string; x: number; y: number }[];
  if (!pts.length) return <Empty description={t("formulation.board.no_pareto", "配方缺少成本或主指标预测，无法绘制帕累托图")} />;
  const front = new Set(paretoFront(pts, "min", "max").map((k) => pts[k].i));
  const option: EChartsOption = {
    tooltip: { formatter: (p: unknown) => { const d = (p as { data: { name: string; value: number[] } }).data; return `${d.name}<br/>${d.value[0]} / ${d.value[1]}`; } },
    legend: { top: 0 },
    xAxis: { type: "value", name: t("formulation.board.cost", "成本 (CNY/kg)"), scale: true },
    yAxis: { type: "value", name: y, scale: true },
    series: [
      { type: "scatter", name: t("formulation.board.front", "帕累托前沿"), symbolSize: 14, data: pts.filter((p) => front.has(p.i)).map((p) => ({ name: p.name, value: [p.x, p.y, p.i] })) },
      { type: "scatter", name: t("formulation.board.dominated", "被支配"), symbolSize: 10, data: pts.filter((p) => !front.has(p.i)).map((p) => ({ name: p.name, value: [p.x, p.y, p.i] })) },
    ],
  };
  return <Chart option={option} height={420} onEvents={{ click: (p: unknown) => { const v = (p as { data?: { value?: number[] } }).data?.value; if (v) onSelect(v[2]); } }} />;
}

function Parallel({ forms }: { forms: Formulation[] }) {
  const { t } = useTranslation();
  const axes: [string, string][] = [["score", "Score"], ["cost_cny_per_kg", t("formulation.metric.cost_cny_per_kg", "成本")],
    ["salt_spray_hours", t("formulation.metric.salt_spray_hours", "耐盐雾")], ["voc_gpl", "VOC"], ["film_weight_gsm", t("formulation.board.film", "膜重")]];
  const option: EChartsOption = {
    tooltip: {},
    parallelAxis: axes.map(([, name], dim) => ({ dim, name })),
    series: [{ type: "parallel", lineStyle: { width: 2 },
      data: forms.map((f) => ({ name: f.name, value: axes.map(([k]) => (k === "score" ? f.score ?? null : f.predicted?.[k] ?? null)) as number[] })) }],
  };
  return <Chart option={option} height={420} />;
}
