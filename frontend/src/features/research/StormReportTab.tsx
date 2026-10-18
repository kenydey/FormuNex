import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Card, Col, Empty, Input, InputNumber, List, Row, Select, Space, Steps, Typography } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, DownloadOutlined, FileAddOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { awaitTask, cancelTask, thinkingSteps, type AsyncTaskAccepted, type ThinkingStep } from "@/api/stream";
import { Markdown } from "@/shared/Markdown";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { saveBlob } from "@/shared/saveFile";
import { postForFile } from "./artifactApi";
import { extractStormToc, type StormTocEntry } from "./stormToc";
import { ThinkingTimeline } from "./TaskThinking";
import { useProjectRequirement } from "./useProjectRequirement";

const STORM_TIMEOUT_MS = 600_000;

interface StormSection {
  section_id: string;
  title: string;
  level?: number;
  core_intent: string;
  target_word_count?: number;
  focal_entities?: string[];
  retrieval_queries: string[];
  depends_on?: string[];
}

interface StormOutline {
  project_id: string;
  topic: string;
  global_summary_goal?: string;
  estimated_total_words?: number;
  perspectives?: string[];
  sections: StormSection[];
  source?: string;
}

type Step = "outline" | "generating" | "report";

/** STORM 长文三步：大纲编辑 → 草稿生成 → 润色/导出。按大纲生成报告（后端 outline 透传）。 */
export default function StormReportTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { projectId } = useProjectRequirement();
  const [topic, setTopic] = useState("");
  const [outline, setOutline] = useState<StormOutline | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("outline");
  const [runMsg, setRunMsg] = useState("");
  const [runSteps, setRunSteps] = useState<ThinkingStep[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("md");
  const abort = useRef<{ ctrl: AbortController; taskId?: string } | null>(null);
  const reportRef = useRef<HTMLDivElement>(null);
  useEffect(() => () => abort.current?.ctrl.abort(), []);

  const toc: StormTocEntry[] = useMemo(() => extractStormToc(markdown ?? ""), [markdown]);

  // 锚点跳页：按文档顺序给渲染出的标题写 id（与 extractStormToc 同序，免去渲染时计数竞态）。
  useEffect(() => {
    const root = reportRef.current;
    if (!root || !markdown) return;
    root.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((el, i) => {
      const entry = toc[i];
      if (entry) el.id = entry.slug;
    });
  }, [markdown, toc]);

  // 进入时恢复已保存的大纲 / 已生成的报告。
  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    void call<StormOutline>("GET", `/api/wiki/storm/outline/${projectId}`)
      .then((o) => { if (alive) { setOutline(o); setTopic(o.topic || ""); } })
      .catch(() => undefined);
    void call<{ markdown?: string }>("GET", `/api/wiki/storm/report/${projectId}`)
      .then((r) => { if (alive && r.markdown) { setMarkdown(r.markdown); setStep("report"); } })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [projectId]);

  const patchSection = (id: string, patch: Partial<StormSection>) =>
    setOutline((o) => (o ? { ...o, sections: o.sections.map((s) => (s.section_id === id ? { ...s, ...patch } : s)) } : o));

  const moveSection = (id: string, dir: -1 | 1) =>
    setOutline((o) => {
      if (!o) return o;
      const i = o.sections.findIndex((s) => s.section_id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= o.sections.length) return o;
      const sections = [...o.sections];
      [sections[i], sections[j]] = [sections[j], sections[i]];
      return { ...o, sections };
    });

  const addSection = () =>
    setOutline((o) => {
      if (!o) return o;
      const n = o.sections.length + 1;
      return {
        ...o,
        sections: [...o.sections, {
          section_id: `sec_custom_${Date.now()}`,
          title: t("research.storm.new_section", "新章节 {{n}}", { n }),
          level: 1, core_intent: "", target_word_count: 500, retrieval_queries: [],
        }],
      };
    });

  const generateOutline = async () => {
    if (!projectId) return;
    setBusy("outline");
    setError(null);
    try {
      const o = await call<StormOutline>("POST", "/api/wiki/storm/outline", {
        project_id: projectId, topic: topic.trim(), max_sections: 6, use_llm: false,
      });
      setOutline(o);
      setTopic(o.topic || topic);
      message.success(t("research.storm.outline_done", "大纲已生成，可编辑后按大纲生成报告"));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const saveOutline = async () => {
    if (!projectId || !outline) return;
    setBusy("save");
    try {
      const saved = await call<StormOutline>("PUT", `/api/wiki/storm/outline/${projectId}`, outline);
      setOutline(saved);
      message.success(t("research.storm.outline_saved", "大纲已保存"));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const generateReport = async () => {
    if (!projectId || !outline || step === "generating") return;
    const ctrl = new AbortController();
    abort.current = { ctrl };
    setError(null);
    setStep("generating");
    setRunMsg(t("research.storm.generating", "正在按大纲逐节起草…"));
    setRunSteps([]);
    try {
      const acc = await call<AsyncTaskAccepted>("POST", "/api/wiki/storm/report", {
        project_id: projectId, topic: outline.topic, outline, use_llm: false,
      });
      abort.current.taskId = acc.task_id;
      await awaitTask(acc.task_id, {
        signal: ctrl.signal, timeoutMs: STORM_TIMEOUT_MS,
        onEvent: (ev) => {
          setRunMsg(ev.message || "");
          const steps = thinkingSteps(ev);
          if (steps.length) setRunSteps(steps);
        },
      });
      const rep = await call<{ markdown?: string }>("GET", `/api/wiki/storm/report/${projectId}`);
      if (!rep.markdown) throw new Error(t("research.storm.no_result", "STORM 未返回报告"));
      setMarkdown(rep.markdown);
      setStep("report");
      message.success(t("research.storm.done", "STORM 长文已生成"));
    } catch (e) {
      setStep("outline");
      setError(ctrl.signal.aborted ? new Error(t("research.storm.cancelled", "STORM 已取消")) : e);
    } finally {
      abort.current = null;
    }
  };

  const cancel = async () => {
    const a = abort.current;
    if (!a) return;
    a.ctrl.abort();
    if (a.taskId) await cancelTask(a.taskId).catch(() => undefined);
  };

  const exportReport = async () => {
    if (!projectId) return;
    setBusy("export");
    try {
      const { blob, filename } = await postForFile("/api/wiki/storm/report/export",
        { project_id: projectId, format: exportFormat }, `storm_report.${exportFormat}`);
      await saveBlob(blob, filename);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const jump = (slug: string) => document.getElementById(slug)?.scrollIntoView({ behavior: "smooth", block: "start" });

  if (!projectId) {
    return <Alert type="info" showIcon message={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />;
  }

  const stepIndex = step === "outline" ? 0 : step === "generating" ? 1 : 2;

  return (
    <>
      <Card>
        <Steps current={stepIndex} items={[
          { title: t("research.storm.step_outline", "大纲编辑") },
          { title: t("research.storm.step_draft", "草稿生成") },
          { title: t("research.storm.step_polish", "润色 / 导出") },
        ]} />
      </Card>

      <Card style={{ marginTop: 16 }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder={t("research.storm.topic", "报告主题，例如：无铬钝化技术综述")} />
          <Button loading={busy === "outline"} onClick={() => void generateOutline()}>
            {t("research.storm.gen_outline", "生成大纲")}</Button>
        </Space.Compact>
      </Card>
      <ErrorAlert error={error} />

      {outline && step !== "report" && (
        <Card style={{ marginTop: 16 }} title={t("research.storm.outline_title", "大纲（可编辑）")}
          extra={(
            <Space>
              <Button icon={<FileAddOutlined />} onClick={addSection}>{t("research.storm.add_section", "添加章节")}</Button>
              <Button loading={busy === "save"} onClick={() => void saveOutline()}>{t("research.storm.save_outline", "保存大纲")}</Button>
              <Button type="primary" icon={<ThunderboltOutlined />} loading={step === "generating"} onClick={() => void generateReport()}>
                {t("research.storm.gen_report", "按大纲生成报告")}</Button>
            </Space>
          )}>
          <List dataSource={outline.sections} rowKey={(s) => s.section_id} renderItem={(s) => (
            <List.Item actions={[
              <Button key="up" size="small" icon={<ArrowUpOutlined />} onClick={() => moveSection(s.section_id, -1)} />,
              <Button key="down" size="small" icon={<ArrowDownOutlined />} onClick={() => moveSection(s.section_id, 1)} />,
              <Button key="del" size="small" danger icon={<DeleteOutlined />}
                onClick={() => setOutline((o) => (o ? { ...o, sections: o.sections.filter((x) => x.section_id !== s.section_id) } : o))} />,
            ]}>
              <List.Item.Meta
                title={<Input value={s.title} onChange={(e) => patchSection(s.section_id, { title: e.target.value })} />}
                description={(
                  <>
                    <Input.TextArea rows={2} value={s.core_intent}
                      onChange={(e) => patchSection(s.section_id, { core_intent: e.target.value })}
                      placeholder={t("research.storm.core_intent", "本节核心意图")} style={{ marginTop: 4 }} />
                    <Space style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary">{t("research.storm.words", "目标字数")}</Typography.Text>
                      <InputNumber size="small" min={100} max={5000} step={100} value={s.target_word_count ?? 500}
                        onChange={(v) => patchSection(s.section_id, { target_word_count: typeof v === "number" ? v : 500 })} />
                      <Input size="small" style={{ width: 320 }} value={(s.retrieval_queries ?? []).join("；")}
                        onChange={(e) => patchSection(s.section_id, { retrieval_queries: e.target.value.split(/[；;]/).map((x) => x.trim()).filter(Boolean) })}
                        placeholder={t("research.storm.queries", "检索问题（分号分隔）")} />
                    </Space>
                  </>
                )} />
            </List.Item>
          )} />
        </Card>
      )}

      {step === "generating" && (
        <Card style={{ marginTop: 16 }} title={t("research.storm.progress", "生成进度")}>
          <Typography.Paragraph>{runMsg}</Typography.Paragraph>
          <ThinkingTimeline steps={runSteps} />
          <Button danger onClick={() => void cancel()}>{t("research.deep.cancel", "取消")}</Button>
        </Card>
      )}

      {step === "report" && markdown && (
        <ProCard style={{ marginTop: 16 }} bordered title={outline?.topic || t("research.storm.report", "STORM 长文")}
          extra={(
            <Space wrap>
              <Select value={exportFormat} onChange={setExportFormat} style={{ width: 100 }}
                options={["md", "docx", "pdf", "pptx"].map((f) => ({ value: f, label: f.toUpperCase() }))} />
              <Button icon={<DownloadOutlined />} loading={busy === "export"} onClick={() => void exportReport()}>
                {t("research.storm.export", "导出")}</Button>
              <Button onClick={() => { setStep("outline"); setMarkdown(null); }}>{t("research.storm.regenerate", "回到大纲重新生成")}</Button>
            </Space>
          )}>
          <Row gutter={16}>
            <Col xs={24} md={5}>
              <div style={{ position: "sticky", top: 8, maxHeight: "70vh", overflowY: "auto" }}>
                <Typography.Title level={5}>{t("research.storm.toc", "目录")}</Typography.Title>
                {toc.length === 0
                  ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("research.storm.no_toc", "暂无标题")} />
                  : toc.map((e) => (
                    <div key={e.slug} style={{ paddingLeft: (e.level - 1) * 14, marginBottom: 4 }}>
                      <Typography.Link onClick={() => jump(e.slug)}>{e.title}</Typography.Link>
                    </div>
                  ))}
              </div>
            </Col>
            <Col xs={24} md={19}>
              <div ref={reportRef}>
                <Markdown content={markdown} mermaid />
              </div>
            </Col>
          </Row>
        </ProCard>
      )}
      {step === "report" && !markdown && <Empty style={{ marginTop: 32 }} description={t("research.storm.empty", "尚无 STORM 长文")} />}
    </>
  );
}
