import { Collapse, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { FormulationExplain } from "./types";

/** 「为何推荐」: objectives hit, constraints missed, constraint trace, KG signals, evidence, supply / uncertainty (legacy FormulaExplainPanel). */
export function ExplainPanel({ explain, score }: { explain?: FormulationExplain | null; score?: number | null }) {
  const { t } = useTranslation();
  if (!explain && score == null) return null;
  const hits = explain?.objectives_hit ?? [];
  const misses = explain?.constraints_miss ?? [];
  const refs = explain?.evidence_refs ?? [];
  const kg = explain?.kg_signals;
  const supply = explain?.supply_flags ?? [];
  const uncertainty = explain?.uncertainty ?? [];
  const notes = explain?.notes ?? [];
  const trace = explain?.effect_trace ?? [];
  const attention = Boolean(misses.length || supply.length || explain?.bias_corrected || trace.some((x) => x.status === "unwired"));
  const section = (title: string, items: string[], color?: "success" | "warning") => items.length > 0 && (
    <div>
      <Typography.Text type="secondary">{title}</Typography.Text>
      <ul style={{ margin: "2px 0 8px", paddingInlineStart: 18 }}>{items.map((x) => <li key={x}><Typography.Text type={color}>{x}</Typography.Text></li>)}</ul>
    </div>
  );
  const traceLabel = (s: string) => s === "wired" ? t("formulation.explain.wired", "生效") : s === "unwired" ? t("formulation.explain.unwired", "未接线") : t("formulation.explain.display", "仅展示");
  return (
    <Collapse size="small" defaultActiveKey={attention ? ["x"] : []} items={[{
      key: "x",
      label: <Space wrap size={4}>
        {t("formulation.explain.title", "为何推荐")}
        {score != null && <Tag color="blue">score {score.toFixed(2)}</Tag>}
        {explain?.bias_corrected && <Tag color="purple">{t("formulation.explain.bias", "已偏差校准")}</Tag>}
      </Space>,
      children: <div data-testid="formula-explain-panel">
        {section(t("formulation.explain.hits", "目标命中"), hits, "success")}
        {section(t("formulation.explain.misses", "约束未满足"), misses, "warning")}
        {trace.length > 0 && <div style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary">{t("formulation.explain.trace", "约束追踪")}</Typography.Text>
          <div><Space wrap size={4}>{trace.slice(0, 12).map((x) => <Tag key={x.field} title={x.detail || x.consumers?.join(",") || undefined}
            color={x.status === "wired" ? "success" : x.status === "unwired" ? "error" : "default"}>{x.label} · {traceLabel(x.status)}</Tag>)}</Space></div>
        </div>}
        {kg && <div style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary">{t("formulation.explain.kg", "KG 信号")}</Typography.Text>
          <div><Space wrap size={4}>
            <Tag color={kg.feasible === false ? "warning" : "success"}>{kg.feasible === false ? t("formulation.explain.incompatible", "不相容") : t("formulation.explain.compatible", "相容")}</Tag>
            {(kg.measured_materials ?? []).slice(0, 4).map((m) => <Tag key={`m-${m}`} color="success">{t("formulation.explain.measured", "实测 {{m}}", { m })}</Tag>)}
            {(kg.synergizes ?? []).slice(0, 3).map((m) => <Tag key={`s-${m}`} color="success">{t("formulation.explain.synergy", "协同 {{m}}", { m })}</Tag>)}
            {(kg.inhibits ?? []).slice(0, 3).map((m) => <Tag key={`i-${m}`} color="warning">{t("formulation.explain.inhibit", "抑制 {{m}}", { m })}</Tag>)}
          </Space></div>
        </div>}
        {refs.length > 0 && <div style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary">{t("formulation.explain.evidence", "证据")}</Typography.Text>
          <div><Space wrap size={4}>{refs.slice(0, 8).map((r, i) => {
            const st = typeof r === "string" ? "ref" : String(r?.source_type ?? "ref");
            const sid = typeof r === "string" ? r : String(r?.source_id ?? "");
            return <Tag key={`${st}:${sid || i}`}><code>{st}:{sid.slice(0, 24)}</code></Tag>;
          })}</Space></div>
        </div>}
        {section(t("formulation.explain.supply", "供应风险"), supply, "warning")}
        {section(t("formulation.explain.uncertainty", "不确定性"), uncertainty)}
        {section(t("formulation.explain.notes", "备注"), notes.slice(0, 4))}
        {!explain && <Typography.Text type="secondary">{t("formulation.explain.score_only", "仅有评分、尚无结构化解释（旧结果或未走推荐打分路径）。")}</Typography.Text>}
      </div>,
    }]} />
  );
}
