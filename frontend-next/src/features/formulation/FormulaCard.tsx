import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Drawer, Dropdown, Popconfirm, Space, Tag, Tooltip, Typography } from "antd";
import {
  ApartmentOutlined, CheckOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, ExportOutlined, HistoryOutlined, LikeOutlined, RobotOutlined, SaveOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { MeasuredMetricHitsBanner } from "@/features/workbench/panels";
import type { MeasuredMetricHit } from "@/features/workbench/measuredHits";
import { errorText } from "@/shared/feedback";
import { fx, saveText, slug } from "./api";
import { ExplainPanel } from "./ExplainPanel";
import { IngredientTable } from "./IngredientTable";
import { formulaToCsv, materialMapOf } from "./logic";
import { ProvenanceDrawer } from "./Provenance";
import { VersionHistory } from "./VersionHistory";
import type { Formulation, Ingredient } from "./types";

const HIDDEN = ["lab_L", "lab_a", "lab_b", "pvc_pct", "cpvc_pct", "pvc_to_cpvc_ratio", "solids_by_volume_pct"];

export interface CardProps {
  form: Formulation;
  rank: number;
  index: number;
  warnings?: string[];
  objectiveMetrics?: Set<string>;
  recommendId?: string | null;
  projectId?: string | null;
  levers?: { name: string; material_map?: Record<string, string> | null }[];
  defaultOpen?: boolean;
  editing?: boolean;
  isDoeBase?: boolean;
  saveBusy?: boolean;
  onToggleEdit?: () => void;
  onIngredientChange?: (ingIdx: number, patch: Partial<Ingredient>) => void;
  onAiModify?: () => void;
  onRemove?: () => void;
  onSaveDoe?: () => void;
  onOpenEditor?: () => void;
}

/** One recommended / leaderboard formulation with all its actions (legacy FormulaLeaderboard › FormulaCard). */
export function FormulaCard(p: CardProps) {
  const { form } = p;
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [provOpen, setProvOpen] = useState(false);
  const outcome = useQuery({ queryKey: ["formulation", "outcome", p.recommendId], enabled: !!p.recommendId, queryFn: () => fx.outcome(p.recommendId!) });
  // Adoption is per formulation: only the card whose name matches the adopted snapshot lights up.
  const mine = !!outcome.data?.formula_name && outcome.data.formula_name === form.name;
  const adopted = mine && outcome.data!.adopted;
  const validated = mine && outcome.data!.experiment_validated;
  const snapshot = () => ({ name: form.name, ingredients: form.ingredients, score: form.score ?? null, material_map: materialMapOf(p.levers) });
  const adopt = useMutation({
    mutationFn: () => fx.adopt(p.recommendId!, { adopt_signal: "button", formula_index: p.index, formula_snapshot: snapshot(), project_id: p.projectId }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["formulation", "outcome", p.recommendId] }),
    onError: (e) => message.error(t("formulation.card.adopt_failed", "采纳失败：{{e}}", { e: errorText(e) })),
  });
  const copyJson = async () => {
    await navigator.clipboard?.writeText(JSON.stringify(form, null, 2));
    message.success(t("formulation.card.copied", "已复制"));
    // Copying is a weak adopt signal (fail-open).
    if (p.recommendId) void fx.adopt(p.recommendId, { adopt_signal: "copied", formula_snapshot: snapshot(), project_id: p.projectId }).catch(() => undefined);
  };
  const pred = form.predicted ?? {};
  const hasColor = pred.lab_L != null && pred.lab_a != null && pred.lab_b != null;
  const ratio = pred.pvc_to_cpvc_ratio;
  const pair = form.kg_compat?.incompatible_pairs?.[0];
  const hits = (form.kg_compat?.measured_metric_hits ?? []) as MeasuredMetricHit[];

  return (
    <ProCard bordered collapsible defaultCollapsed={!p.defaultOpen} style={{ marginBottom: 12 }}
      title={<Space wrap size={4}>
        <Typography.Text type="secondary">#{p.rank}</Typography.Text>
        {hasColor && <Tooltip title={`L*=${pred.lab_L.toFixed(1)} a*=${pred.lab_a.toFixed(1)} b*=${pred.lab_b.toFixed(1)}${pred.delta_e != null ? `  ΔE00=${pred.delta_e.toFixed(1)}` : ""}`}>
          <span style={{ display: "inline-block", width: 14, height: 14, borderRadius: 3, background: `lab(${pred.lab_L} ${pred.lab_a} ${pred.lab_b})` }} />
        </Tooltip>}
        <Typography.Text strong>{form.name}</Typography.Text>
        {(form.bias_corrected_metrics?.length ?? 0) > 0 && <Tooltip title={form.bias_corrected_metrics!.join("、")}><Tag color="purple">{t("formulation.card.calibrated", "已校准")}</Tag></Tooltip>}
        {form.source === "ai_modify" && <Tag color="geekblue">{t("formulation.card.ai_modified", "AI 修改")}</Tag>}
        {form.source === "manual" && <Tag>{t("formulation.card.manual", "手动")}</Tag>}
        {p.isDoeBase && <Tooltip title={t("formulation.card.doe_base_tip", "当前 DOE 基准配方——生成 DOE 时因子将围绕此配方的实际用量设计")}><Tag color="success">{t("formulation.card.doe_base", "DOE 基准")}</Tag></Tooltip>}
        {p.editing && <Tag color="processing">{t("formulation.card.editing", "编辑中")}</Tag>}
        {adopted && <Tag color="blue">{validated ? t("formulation.card.validated", "已采纳 · 已验证") : t("formulation.card.adopted", "已采纳")}</Tag>}
      </Space>}
      extra={<Space onClick={(e) => e.stopPropagation()}>
        {form.score != null && <Typography.Text strong>{form.score.toFixed(2)}</Typography.Text>}
        <Dropdown menu={{ items: [
          { key: "json", label: t("formulation.card.copy_json", "复制 JSON"), onClick: () => void copyJson() },
          { key: "csv", label: t("formulation.card.csv", "导出 CSV"), icon: <DownloadOutlined />, onClick: () => saveText(formulaToCsv(form), `${slug(form.name)}.csv`) },
        ] }}><Button size="small" icon={<ExportOutlined />}>{t("formulation.card.export", "导出")}</Button></Dropdown>
        <Dropdown menu={{ items: [
          { key: "ip", label: t("formulation.card.ip", "专利 / IP 分析"), onClick: () => navigate("/knowledge/research/ip", { state: { formulation: form } }) },
          { key: "similar", label: t("formulation.card.similar", "相似历史配方"), onClick: () => navigate("/lab/similar", { state: { formulation: form } }) },
          { key: "substitution", label: t("formulation.card.substitution", "原料替换"),
            onClick: () => navigate(`/lab/substitution?material=${encodeURIComponent(form.ingredients[0]?.name ?? "")}`) },
        ] }}><Button size="small">{t("formulation.card.analyze", "分析")}</Button></Dropdown>
      </Space>}
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        {pred.pvc_pct != null && <Space wrap size={4}>
          <Tag>PVC {pred.pvc_pct.toFixed(1)}%</Tag>
          {pred.cpvc_pct != null && <Tag>CPVC {pred.cpvc_pct.toFixed(1)}%</Tag>}
          {ratio != null && <Tooltip title={ratio < 1 ? t("formulation.card.pvc_good", "PVC < CPVC：屏蔽性好") : t("formulation.card.pvc_bad", "PVC > CPVC：漆膜多孔、屏蔽性差")}>
            <Tag color={ratio < 1 ? "success" : "warning"}>PVC/CPVC {ratio.toFixed(2)}</Tag></Tooltip>}
          {pred.solids_by_volume_pct != null && <Tag>SBV {pred.solids_by_volume_pct.toFixed(1)}%</Tag>}
        </Space>}
        <Space wrap size={4}>
          {Object.entries(pred).filter(([k]) => !HIDDEN.includes(k)).map(([k, v]) => {
            const std = form.predicted_std?.[k];
            const high = std != null && std > Math.abs(v) * 0.2;
            return <Tag key={k} color={p.objectiveMetrics?.has(k) ? "blue" : undefined}>
              {k}: <b>{v}</b>{std != null && <Typography.Text type={high ? "warning" : "secondary"}> ±{std.toFixed(std < 1 ? 3 : 1)}</Typography.Text>}
              {form.prediction_tiers?.[k] && <Typography.Text type="secondary"> {form.prediction_tiers[k]}</Typography.Text>}
            </Tag>;
          })}
        </Space>
        <IngredientTable ingredients={form.ingredients} editable={p.editing} onChange={p.onIngredientChange} warnings={p.warnings} />
        {p.editing && <Typography.Text type="secondary">{t("formulation.card.edit_hint", "编辑模式：直接修改组分后点「完成」保存到项目（仅影响当前卡片）")}</Typography.Text>}
        {(form.warnings?.length ?? 0) > 0 && <Alert type="warning" showIcon message={form.warnings!.join("；")} />}
        {form.kg_compat && form.kg_compat.feasible === false && pair && <Alert type="warning" showIcon
          message={t("formulation.card.incompatible", "{{m}} 不相容，建议在材料库中查找实测优先的替代料", { m: pair.a ?? form.ingredients[0]?.name ?? "" })} />}
        {hits.length > 0 ? <MeasuredMetricHitsBanner hits={hits} />
          : (form.kg_compat?.measured_materials?.length ?? 0) > 0 && <Alert type="success" showIcon
            message={t("formulation.card.measured", "实测验证：{{m}} 已获实测证据加成", { m: form.kg_compat!.measured_materials!.join("、") })} />}
        <ExplainPanel explain={form.explain} score={form.score} />
        <Space wrap>
          {p.onToggleEdit && <Button size="small" type={p.editing ? "primary" : "default"} icon={p.editing ? <CheckOutlined /> : <EditOutlined />} onClick={p.onToggleEdit}>
            {p.editing ? t("formulation.card.done", "完成") : t("formulation.card.edit", "修改")}</Button>}
          {p.onAiModify && <Tooltip title={t("formulation.card.ai_tip", "仅以此配方为基准，AI 按你的要求生成修改变体（追加为新卡片）")}>
            <Button size="small" icon={<RobotOutlined />} onClick={p.onAiModify}>{t("formulation.card.ai", "AI 修改")}</Button></Tooltip>}
          {p.onSaveDoe && <Tooltip title={t("formulation.card.doe_tip", "保存到版本库并设为 DOE 基准配方（因子将围绕此配方设计）")}>
            <Button size="small" icon={<SaveOutlined />} loading={p.saveBusy} type={p.isDoeBase ? "primary" : "default"} ghost={p.isDoeBase} onClick={p.onSaveDoe}>
              {p.isDoeBase ? t("formulation.card.doe_base", "DOE 基准") : t("formulation.card.save_doe", "保存为 DOE 基准")}</Button></Tooltip>}
          {p.recommendId && <Tooltip title={t("formulation.card.adopt_tip", "标记采纳该配方（推荐质量抽检埋点）")}>
            <Button size="small" icon={<LikeOutlined />} loading={adopt.isPending} disabled={adopted} onClick={() => adopt.mutate()} data-testid="adopt-button">
              {adopted ? (validated ? t("formulation.card.validated", "已采纳 · 已验证") : t("formulation.card.adopted", "已采纳")) : t("formulation.card.adopt", "采纳")}</Button></Tooltip>}
          {p.onOpenEditor && <Button size="small" type="primary" onClick={p.onOpenEditor}>{t("formulation.card.to_editor", "采纳到编辑器")}</Button>}
          <Button size="small" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>{t("formulation.card.history", "修订历史")}</Button>
          <Tooltip title={t("formulation.card.prov_tip", "查看该配方的证据谱系：结论 ← 文献 / 实验运行")}>
            <Button size="small" icon={<ApartmentOutlined />} onClick={() => setProvOpen(true)}>{t("formulation.prov.open", "证据谱系")}</Button></Tooltip>
          {p.onRemove && <Popconfirm title={t("formulation.card.remove_q", "从当前推荐列表删除该配方？")} description={t("formulation.card.remove_desc", "版本库中已保存的历史不受影响。")} onConfirm={p.onRemove}>
            <Button size="small" danger icon={<DeleteOutlined />}>{t("common.delete", "删除")}</Button></Popconfirm>}
        </Space>
      </Space>
      <Drawer open={historyOpen} onClose={() => setHistoryOpen(false)} width={760} destroyOnHidden title={t("formulation.card.history_title", "修订历史 · {{n}}", { n: form.name })}>
        <VersionHistory formulation={form} />
      </Drawer>
      <ProvenanceDrawer open={provOpen} onClose={() => setProvOpen(false)} nodeType="formulation" nodeId={form.client_uid ?? form.name} title={form.name} />
    </ProCard>
  );
}
