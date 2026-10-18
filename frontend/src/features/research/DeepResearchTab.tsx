import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Card, Descriptions, Empty, Input, Select, Space, Table, Tag } from "antd";
import { DownloadOutlined, InboxOutlined, ThunderboltOutlined, BranchesOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, cancelTask, thinkingSteps, type AsyncTaskAccepted, type ThinkingStep } from "@/api/stream";
import { Markdown } from "@/shared/Markdown";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { saveAsVersion, saveText, saveToShelf } from "./artifactApi";
import { deepPercent, evidenceKey, sanitizeEvidence, shelfFilename } from "./lib";
import { CitationTag } from "./ResearchChatTab";
import { useResearch } from "./store";
import { CragSteps, ThinkingTimeline } from "./TaskThinking";
import type { ComprehensiveReport, Formulation } from "./types";
import { useDomainOptions, useProjectRequirement } from "./useProjectRequirement";

const DEEP_TIMEOUT_MS = 600_000;

interface RunState { stage: string; message: string; progress?: number; steps: ThinkingStep[] }

/** CRAG deep research over the gathered sources + KB: a cited report with candidate formulations. */
export default function DeepResearchTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { projectId, requirement, domain: projectDomain } = useProjectRequirement();
  const domains = useDomainOptions();
  const { query, sources, report, setReport, addSources, setTurns } = useResearch();
  // A chat playbook hands its question over as ``?q=`` or location state ``{ question }``.
  const [params] = useSearchParams();
  const location = useLocation();
  const handed = params.get("q") || (location.state as { question?: string } | null)?.question || "";
  const [topic, setTopic] = useState(handed || query);
  const [domain, setDomain] = useState(projectDomain);
  useEffect(() => setDomain(projectDomain), [projectDomain]);
  const [run, setRun] = useState<RunState | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const abort = useRef<{ ctrl: AbortController; taskId?: string } | null>(null);
  useEffect(() => () => abort.current?.ctrl.abort(), []);

  const start = async () => {
    const tp = (topic || query).trim();
    if (!tp || run) return;
    const ctrl = new AbortController();
    abort.current = { ctrl };
    setError(null);
    setRun({ stage: "retrieve", message: t("research.deep.retrieving", "正在检索"), steps: [] });
    try {
      const acc = await call<AsyncTaskAccepted>("POST", "/api/research/deep", {
        topic: tp, query: query.trim() || tp, sources: sources.map(sanitizeEvidence),
        requirement: { ...(requirement ?? {}), domain, ...(projectId ? { project_id: projectId } : {}) },
      });
      abort.current.taskId = acc.task_id;
      const final = await awaitTask(acc.task_id, {
        signal: ctrl.signal, timeoutMs: DEEP_TIMEOUT_MS,
        onEvent: (ev) => setRun((r) => {
          const steps = thinkingSteps(ev);
          return {
            stage: ev.stage || r?.stage || "",
            message: ev.stage === "retrieve" && !ev.message ? t("research.deep.cold", "模型冷启动中… 正在检索") : ev.message ?? "",
            progress: ev.progress, steps: steps.length ? steps : r?.steps ?? [],
          };
        }),
      });
      const rep = (final.data as { report?: ComprehensiveReport } | undefined)?.report;
      if (!rep) throw new Error(t("research.deep.no_result", "深度研究未返回结果"));
      setReport(rep);
      if (rep.citations?.length) addSources(rep.citations);
      setTurns((ts) => [...ts, { role: "assistant", content: rep.report_markdown, citations: rep.citations }]);
      message.success(t("research.deep.done", "深度研究报告已生成"));
    } catch (e) {
      setError(ctrl.signal.aborted ? new Error(t("research.deep.cancelled", "深度研究已取消")) : e);
    } finally {
      setRun(null);
      abort.current = null;
    }
  };

  const cancel = async () => {
    const a = abort.current;
    if (!a) return;
    a.ctrl.abort();
    if (a.taskId) await cancelTask(a.taskId).catch(() => undefined);
  };

  const guard = async (key: string, f: () => Promise<void>) => {
    setSaving(key);
    try {
      await f();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(null);
    }
  };

  return (
    <>
      <Card>
        <Space.Compact style={{ width: "100%" }}>
          <Select style={{ width: 160 }} value={domain} onChange={setDomain} options={domains} />
          <Input value={topic} onChange={(e) => setTopic(e.target.value)} onPressEnter={() => void start()}
            placeholder={t("research.deep.topic", "研究主题，例如：无铬钝化在镀锌钢上的耐蚀机理与配方")} />
          {run ? <Button danger onClick={() => void cancel()}>{t("research.deep.cancel", "取消")}</Button>
            : <Button type="primary" icon={<ThunderboltOutlined />} disabled={!(topic || query).trim()} onClick={() => void start()}>
              {t("research.deep.run", "开始深度研究")}</Button>}
        </Space.Compact>
        <div style={{ marginTop: 8 }}>
          <Tag>{t("research.deep.using", "将使用 {{n}} 条已收集资料 + 知识库", { n: sources.length })}</Tag>
        </div>
      </Card>
      <ErrorAlert error={error} />
      {run && (
        <Card style={{ marginTop: 16 }} title={t("research.deep.progress", "研究进度")}>
          <CragSteps stage={run.stage} percent={deepPercent(run.stage, run.progress)} message={run.message} />
          <ThinkingTimeline steps={run.steps} />
        </Card>
      )}
      {!run && !report && <Empty style={{ marginTop: 32 }} description={t("research.deep.empty", "尚无深度研究报告")} />}
      {report && (
        <ProCard style={{ marginTop: 16 }} bordered title={report.topic || t("research.deep.report", "深度研究报告")}
          extra={(
            <Space wrap>
              <Button icon={<DownloadOutlined />} onClick={() => saveText(report.report_markdown, shelfFilename("deep_report", "md"))}>
                {t("research.deep.download", "下载 MD")}</Button>
              <Button icon={<InboxOutlined />} disabled={!projectId} loading={saving === "shelf"} onClick={() => void guard("shelf", async () => {
                await saveToShelf(projectId!, shelfFilename("deep_report", "md"), report.report_markdown);
                message.success(t("research.deep.shelved", "已保存到项目导出货架"));
              })}>{t("research.deep.shelf", "保存到货架")}</Button>
              <Button icon={<BranchesOutlined />} disabled={!projectId} loading={saving === "version"} onClick={() => void guard("version", async () => {
                const lin = await saveAsVersion(projectId!, t("research.deep.lineage", "深度研究 · {{t}}", { t: report.topic }).slice(0, 250), report.report_markdown);
                navigate(`/knowledge/research/artifacts/${lin.lineage_id}`);
              })}>{t("research.deep.version", "存为版本")}</Button>
            </Space>
          )}>
          {!projectId && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />}
          <Descriptions size="small" column={4} style={{ marginBottom: 12 }} items={[
            { key: "e", label: t("research.deep.engine", "引擎"), children: report.engine },
            { key: "w", label: t("research.deep.web", "外部文献"), children: report.web_count },
            { key: "k", label: t("research.deep.kb", "知识库片段"), children: report.kb_count },
            { key: "c", label: t("research.deep.cites", "引用"), children: report.citations?.length ?? 0 },
          ]} />
          <Markdown content={report.report_markdown} mermaid />
          {(report.citations?.length ?? 0) > 0 && (
            <ProCard title={t("research.deep.citations", "引用")} ghost style={{ marginTop: 16 }}>
              {report.citations.map((c, i) => <CitationTag key={`${evidenceKey(c)}-${i}`} ev={c} />)}
            </ProCard>
          )}
          {(report.candidates?.length ?? 0) > 0 && (
            <ProCard title={t("research.deep.candidates", "候选配方")} ghost style={{ marginTop: 16 }}>
              <Table<Formulation> size="small" rowKey={(f, i) => `${f.name}-${i}`} pagination={false} dataSource={report.candidates}
                expandable={{ expandedRowRender: (f) => (
                  <Table size="small" pagination={false} rowKey={(r, i) => `${r.name}-${i}`} dataSource={f.ingredients}
                    columns={[{ title: t("research.emb.material", "原料"), dataIndex: "name" }, { title: t("research.emb.role", "角色"), dataIndex: "role" },
                      { title: "wt%", dataIndex: "weight_pct", align: "right" }, { title: "CAS", dataIndex: "cas_no" }]} />
                ) }}
                columns={[
                  { title: t("research.deep.cand_name", "名称"), dataIndex: "name" },
                  { title: t("research.deep.cand_n", "组分数"), render: (_v, f) => f.ingredients?.length ?? 0, width: 90 },
                  { title: t("research.deep.cand_score", "评分"), dataIndex: "score", width: 90, render: (v) => (typeof v === "number" ? v.toFixed(3) : "—") },
                  { title: t("research.deep.cand_why", "依据"), dataIndex: "rationale", ellipsis: true },
                ]} />
            </ProCard>
          )}
        </ProCard>
      )}
    </>
  );
}
