import { Progress, Steps, Tag, Timeline, Typography } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { ThinkingStep } from "@/api/stream";
import { CRAG_STAGES, stageIndex } from "./lib";

/** CRAG stage bar of a deep-research run (检索 → 评估 → 补搜 → 生成 → 核验 → 修正). */
export function CragSteps({ stage, percent, message }: { stage: string; percent: number; message?: string }) {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    retrieve: t("research.stage.retrieve", "检索"), grade: t("research.stage.grade", "评估"), fallback: t("research.stage.fallback", "补搜"),
    generate: t("research.stage.generate", "生成"), claim_check: t("research.stage.claim_check", "核验"), regenerate: t("research.stage.regenerate", "修正"),
  };
  return (
    <div>
      <Steps size="small" current={stageIndex(stage)} status="process" items={CRAG_STAGES.map((s) => ({ title: labels[s.id] ?? s.zh }))} />
      <Progress percent={percent} status="active" style={{ marginTop: 12 }} />
      {message && <Typography.Text type="secondary">{message}</Typography.Text>}
    </div>
  );
}

/** The task's thinking chain (stage / thought / tool steps) as a timeline. */
export function ThinkingTimeline({ steps }: { steps: ThinkingStep[] }) {
  const { t } = useTranslation();
  if (!steps.length) return null;
  const kind: Record<string, string> = {
    stage: t("research.thinking.stage", "阶段"), thought: t("research.thinking.thought", "思考"), tool: t("research.thinking.tool", "工具"),
  };
  return (
    <Timeline style={{ marginTop: 16 }} items={steps.map((s) => ({
      key: s.id,
      color: s.status === "error" ? "red" : s.status === "done" ? "green" : s.status === "running" ? "blue" : "gray",
      dot: s.status === "running" ? <LoadingOutlined /> : s.status === "done" ? <CheckCircleOutlined /> : s.status === "error" ? <CloseCircleOutlined /> : undefined,
      children: (
        <div>
          {s.kind && <Tag>{kind[s.kind] ?? s.kind}</Tag>}
          <Typography.Text strong>{s.title}</Typography.Text>
          {s.detail && <div><Typography.Text type="secondary" style={{ whiteSpace: "pre-wrap" }}>{s.detail}</Typography.Text></div>}
        </div>
      ),
    }))} />
  );
}
