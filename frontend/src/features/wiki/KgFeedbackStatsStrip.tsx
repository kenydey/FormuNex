import { Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import type { KgFeedbackStats } from "./types";

/** Measured-feedback counts of the KG (G5), shown without an entity search. ``compact`` hides an empty library. */
export function KgFeedbackStatsStrip({ refreshKey = 0, compact = false }: { refreshKey?: number; compact?: boolean }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["kg", "feedback-stats", refreshKey], queryFn: () => call<KgFeedbackStats>("GET", "/api/kg/feedback/stats"), retry: false });
  const s = q.data;
  if (!s) return null;
  if (compact && s.measured_total <= 0) return null;
  const parts = [s.measured_total <= 0
    ? t("wiki.kg_feedback_zero", "实测反馈库：0 条（实验台账完成同步后回流）")
    : t("wiki.kg_feedback_total", "实测反馈库：{{n}} 条", { n: s.measured_total })];
  if (s.measured_total > 0) {
    if (s.measured_performance > 0) parts.push(t("wiki.kg_feedback_perf", "性能 {{n}}", { n: s.measured_performance }));
    if ((s.measured_material ?? 0) > 0) parts.push(t("wiki.kg_feedback_material", "材料级 {{n}}", { n: s.measured_material }));
    if ((s.measured_domain ?? 0) > 0) parts.push(t("wiki.kg_feedback_domain", "领域级 {{n}}", { n: s.measured_domain }));
  }
  return <Typography.Text type="secondary" data-testid="kg-feedback-stats">{parts.join(" · ")}</Typography.Text>;
}
