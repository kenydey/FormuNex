import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, MinusCircleOutlined } from "@ant-design/icons";
import { Collapse, Tag, Timeline, Typography, theme } from "antd";
import { useTranslation } from "react-i18next";
import type { ThinkingStep } from "@/api/stream";

/** Human title of a step: chat phases are translated, everything else is shown as sent. */
export function useStepTitle() {
  const { t } = useTranslation();
  return (s: ThinkingStep) => {
    switch (s.title) {
      case "retrieval": return t("chat.phase.retrieval", "检索资料");
      case "tools": return t("chat.phase.tools", "调用工具");
      case "answering": return t("chat.phase.answering", "生成回答");
      case "claims": return t("chat.phase.claims", "核对引用与断言");
      case "chart_vlm": return t("chat.phase.chart_vlm", "图表视觉解读");
      case "pageindex": return t("chat.phase.pageindex", "深度模式：沿目录树逐节阅读（PageIndex）");
      default: return s.title;
    }
  };
}

/** Collapsible thinking / tool timeline (chat stream phases and task ``thinking`` snapshots). */
export function ThinkingTimeline({ steps, title, defaultOpen = false }: { steps: ThinkingStep[]; title?: string; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const stepTitle = useStepTitle();
  if (!steps.length) return null;
  const icon = (s: ThinkingStep) => {
    switch (s.status) {
      case "done": return <CheckCircleOutlined style={{ color: token.colorSuccess }} />;
      case "error": return <CloseCircleOutlined style={{ color: token.colorError }} />;
      case "pending": return <MinusCircleOutlined style={{ color: token.colorTextQuaternary }} />;
      default: return <LoadingOutlined style={{ color: token.colorPrimary }} />;
    }
  };
  return (
    <Collapse size="small" ghost defaultActiveKey={defaultOpen ? ["t"] : []} data-testid="thinking-timeline" items={[{
      key: "t",
      label: <Typography.Text type="secondary">{title ?? t("chat.thinking", "思考链路")} · {t("chat.steps", "{{n}} 步", { n: steps.length })}</Typography.Text>,
      children: (
        <Timeline style={{ marginBottom: -16 }} items={steps.map((s) => ({
          key: s.id,
          dot: icon(s),
          children: (
            <div>
              <Typography.Text strong={s.status === "running"}>{stepTitle(s)}</Typography.Text>
              {s.kind && s.kind !== "stage" && <Tag style={{ marginLeft: 6 }}>{s.kind}</Tag>}
              {s.detail && s.detail !== s.title && <div><Typography.Text type="secondary">{s.detail}</Typography.Text></div>}
            </div>
          ),
        }))} />
      ),
    }]} />
  );
}
