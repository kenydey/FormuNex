import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Checkbox, Descriptions, Empty, Input, Progress, Space, Steps, Tag, Typography } from "antd";
import { StopOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { errorText } from "@/shared/feedback";
import { fx } from "./api";
import { useOpenInEditor } from "./adopt";
import { FormulaCard } from "./FormulaCard";
import { ModeSelector } from "./ModeSelector";
import type { ResearchResult } from "./types";
import { useResearchTask } from "./useTask";
import { groundingSources, useWorkspace } from "./workspace";

/** AI formulation recommendation for the current project; results go to the project's leaderboard and can be adopted
 * into the formulation editor (legacy recommend flow + RecommendedFormulaTable + FormulationModeSelector). */
export default function RecommendPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { projectId, project, ws, requirement, patch } = useWorkspace();
  const task = useResearchTask();
  const openInEditor = useOpenInEditor();
  const [preferCatalog, setPreferCatalog] = useState(false);
  const [params] = useSearchParams();
  const location = useLocation();
  // A chat playbook hands over the current question (``?q=`` / location state) as the extra retrieval query.
  const [query, setQuery] = useState(() => params.get("q") ?? (location.state as { question?: string } | null)?.question ?? "");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState<ResearchResult | null>(null);
  const shown = round ?? ws?.research ?? null;
  const sources = groundingSources(ws);

  async function run() {
    if (!requirement || !projectId) return;
    setError(null);
    try {
      const research = await task.run("/api/research/recommend", {
        ...requirement, project_id: projectId, sources, query: query.trim() || ws?.search_query || "", prefer_materials_catalog: preferCatalog, relation_insight: true,
      });
      let forms = research.recommended ?? [];
      try {
        const v = await fx.validate(forms, requirement);
        forms = v.formulations ?? forms;
        setWarnings(v.warnings ?? []);
      } catch { /* enrichment is best-effort */ }
      const next = { ...research, recommended: forms };
      setRound(next);
      await patch.mutateAsync({ research: next, leaderboard: forms });
      message.success(t("formulation.rec.done", "推荐完成：{{n}} 个配方已写入项目排行榜", { n: forms.length }));
    } catch (e) {
      const msg = errorText(e);
      setError(/取消|cancel|停止/i.test(msg) ? t("formulation.rec.cancelled", "推荐已取消") : msg);
    }
  }

  if (!projectId) return <PageContainer><Alert type="info" showIcon message={t("formulation.need_project", "请先在顶部选择项目")} /></PageContainer>;
  const objectives = requirement?.objectives ?? [];
  return (
    <PageContainer loading={project.isLoading}
      extra={<Link to="/lab/leaderboard"><Button>{t("formulation.rec.to_board", "查看排行榜")}</Button></Link>}>
      <ProCard gutter={16} ghost wrap>
        <ProCard colSpan={{ xs: 24, lg: 15 }} bordered title={t("formulation.rec.req", "研发需求")}>
          {!requirement ? <Alert type="warning" showIcon message={t("formulation.rec.no_req", "该项目尚未定义研发需求")}
            action={<Link to="/spec">{t("formulation.rec.go_spec", "去定义")}</Link>} /> : <>
            <Descriptions size="small" column={{ xs: 1, md: 2 }} items={[
              { key: "d", label: t("formulation.exp.domain", "产品领域"), children: requirement.domain },
              { key: "s", label: t("formulation.rec.substrate", "基材"), children: String(requirement.substrate ?? "—") },
              { key: "o", label: t("formulation.rec.objectives", "优化目标"), span: "filled",
                children: objectives.length ? <Space wrap size={4}>{objectives.map((o) => <Tag key={o.metric}>{o.display_name || o.metric} · {o.direction}</Tag>)}</Space> : "—" },
              { key: "e", label: t("formulation.rec.evidence", "依据证据"), span: "filled", children: t("formulation.rec.evidence_n", "{{n}} 条知识源", { n: sources.length }) },
            ]} />
            <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("formulation.rec.query_ph", "补充检索关键词（可选，默认用需求摘要）")} />
              <Checkbox checked={preferCatalog} onChange={(e) => setPreferCatalog(e.target.checked)}>{t("formulation.rec.prefer_catalog", "优先使用材料库中的原料（软偏好，不做硬过滤）")}</Checkbox>
              <Space>
                <Button type="primary" icon={<ThunderboltOutlined />} loading={task.busy} onClick={() => void run()}>{t("formulation.rec.run", "生成推荐")}</Button>
                {task.busy && <Button icon={<StopOutlined />} onClick={() => void task.cancel()}>{t("formulation.rec.cancel", "取消")}</Button>}
              </Space>
            </Space>
          </>}
          {task.busy && <div style={{ marginTop: 16 }}>
            <Progress percent={Math.round((task.event?.progress ?? 0) * (task.event?.progress != null && task.event.progress <= 1 ? 100 : 1))} size="small" />
            <Typography.Text type="secondary">{task.event?.message || (task.event?.stage === "retrieve" || !task.event ? t("formulation.rec.cold", "模型冷启动中… 正在检索") : task.event.stage)}</Typography.Text>
            {task.steps.length > 0 && <Steps direction="vertical" size="small" style={{ marginTop: 8 }} items={task.steps.map((s) => ({
              key: s.id, title: s.title, description: s.detail,
              status: s.status === "done" ? "finish" : s.status === "error" ? "error" : s.status === "running" ? "process" : "wait",
            }))} />}
          </div>}
          {error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={error} />}
        </ProCard>
        <ProCard colSpan={{ xs: 24, lg: 9 }} ghost><ModeSelector /></ProCard>
      </ProCard>
      <ProCard ghost style={{ marginTop: 16 }} title={<Space>
        {round ? t("formulation.rec.results", "本轮推荐") : t("formulation.rec.last", "上一轮推荐")}
        {shown?.recommend_engine && <Tag color={shown.recommend_engine === "llm" ? "blue" : "default"}>{shown.recommend_engine === "llm" ? t("formulation.rec.llm", "LLM 推荐") : t("formulation.rec.offline", "离线模板")}</Tag>}
      </Space>}>
        {warnings.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("formulation.board.validate", "配方校验 / 目录补全提示")}
          description={<ul style={{ margin: 0, paddingInlineStart: 18 }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />}
        {!shown?.recommended?.length ? <Empty description={t("formulation.rec.none", "尚无推荐结果")} /> : shown.recommended.map((f, i) => (
          <FormulaCard key={`${f.name}-${i}`} form={f} rank={i + 1} index={i} defaultOpen={i === 0} warnings={warnings}
            objectiveMetrics={new Set(objectives.map((o) => o.metric))} recommendId={shown.recommend_id} projectId={projectId} levers={requirement?.levers}
            onOpenEditor={() => openInEditor(f, { recommendId: shown.recommend_id, index: i, projectId, levers: requirement?.levers })} />
        ))}
      </ProCard>
    </PageContainer>
  );
}
