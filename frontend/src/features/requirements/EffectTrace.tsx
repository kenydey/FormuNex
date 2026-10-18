import { ProCard } from "@ant-design/pro-components";
import { Space, Tag, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { CONSTRAINT_CATALOG, type ConstraintKey } from "./constraints";
import { traceCounts, type TraceItem } from "./effectTrace";
import { constraintLabel, consumerLabel } from "./labels";

const CATALOG_KEYS = new Set<string>(CONSTRAINT_CATALOG.map((c) => c.key));
const STATUS_COLOR: Record<string, string> = { wired: "success", unwired: "error", display_only: "default" };

/** 约束追踪 (legacy ConstraintEffectTrace): which requirement fields reach scoring / DOE. */
export function EffectTrace({ items, title }: { items?: TraceItem[] | null; title?: string }) {
  const { t } = useTranslation();
  if (!items?.length) return null;
  const counts = traceCounts(items);
  const label = (i: TraceItem) => {
    if (i.field === "objectives") return t("requirements.trace.default_objectives", "（默认域目标）");
    if (i.field === "levers") return t("requirements.trace.default_levers", "（域默认杠杆）");
    if (i.field === "notes") return t("requirements.trace.notes", "备注");
    if (CATALOG_KEYS.has(i.field) && i.value != null) return `${constraintLabel(t, i.field as ConstraintKey)} = ${i.value}`;
    return i.label;
  };
  const detail = (i: TraceItem) => {
    if (i.detail === "resolve_levers") return t("requirements.trace.levers_detail", "由 resolve_levers 按领域回填");
    if (i.detail === "llm_prompt") return t("requirements.trace.notes_detail", "进入 LLM prompt，不进数值评分");
    return i.detail;
  };
  const statusText = (s: string) =>
    s === "wired" ? t("requirements.trace.wired", "生效") : s === "unwired" ? t("requirements.trace.unwired", "未接线") : t("requirements.trace.display_only", "仅展示");
  return (
    <ProCard size="small" bordered title={title ?? t("requirements.trace.title", "约束追踪")} data-testid="constraint-effect-trace"
      extra={<Typography.Text type="secondary">
        {t("requirements.trace.counts", "生效 {{w}} · 未接线 {{u}} · 仅展示 {{d}}", { w: counts.wired, u: counts.unwired, d: counts.display_only })}
      </Typography.Text>}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {t("requirements.trace.help", "哪些需求字段进入了配方推荐评分 / DOE；“仅展示”的字段只出现在提示词或报告中。")}
      </Typography.Paragraph>
      <Space size={[4, 4]} wrap>
        {items.map((i) => {
          const tip = [detail(i), (i.consumers ?? []).map((c) => consumerLabel(t, c)).join(", ")].filter(Boolean).join(" · ");
          return (
            <Tooltip key={i.field} title={tip || undefined}>
              <Tag color={STATUS_COLOR[i.status] ?? "default"} data-status={i.status}>
                {label(i)} · {statusText(i.status)}
              </Tag>
            </Tooltip>
          );
        })}
      </Space>
    </ProCard>
  );
}
