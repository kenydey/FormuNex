import { Alert, Space, Tag, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { RelationInsight } from "./types";

/** KG relation-insights banner of the last recommend round (legacy FormulaLeaderboard ``relation-insights-banner``). */
export function RelationInsightsBanner({ insights, max = 6 }: { insights?: RelationInsight[] | null; max?: number }) {
  const { t } = useTranslation();
  const list = insights ?? [];
  if (!list.length) return null;
  return (
    <Alert type="info" showIcon style={{ marginBottom: 12 }} data-testid="relation-insights-banner"
      message={t("formulation.board.kg_insights", "KG 关系洞察 · {{n}}", { n: list.length })}
      description={<Space direction="vertical" size={2} style={{ width: "100%" }}>
        {list.slice(0, max).map((ins, i) => (
          <div key={`${ins.component ?? "?"}-${i}`}>
            <Typography.Text strong>{ins.component || "?"}</Typography.Text>
            {ins.cas_no && <Typography.Text type="secondary"> ({ins.cas_no})</Typography.Text>}
            {(ins.substitutes?.length ?? 0) > 0 && <>
              <Typography.Text type="secondary"> → {t("formulation.board.kg_subs", "可替代")} </Typography.Text>
              <Tooltip title={ins.substitutes!.join("、")}>
                <span>{ins.substitutes!.slice(0, 3).map((s) => <Tag key={s}>{s}</Tag>)}</span>
              </Tooltip>
            </>}
            {(ins.relations?.length ?? 0) > 0 && (
              <Tooltip title={ins.relations!.map((r) => `${r.type ?? ""} → ${r.target ?? ""}${r.confidence != null ? ` (${r.confidence.toFixed(2)})` : ""}`).join("\n")}>
                <Typography.Text type="secondary"> · {t("formulation.board.kg_rels", "关系 {{n}} 条", { n: ins.relations!.length })}</Typography.Text>
              </Tooltip>
            )}
          </div>
        ))}
        {list.length > max && <Typography.Text type="secondary">{t("formulation.board.kg_more", "另有 {{n}} 条未显示", { n: list.length - max })}</Typography.Text>}
      </Space>} />
  );
}
