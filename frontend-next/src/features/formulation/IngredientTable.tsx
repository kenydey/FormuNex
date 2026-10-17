import { Input, Space, Table, Tag, Tooltip, Typography, theme } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fx } from "./api";
import { casStatus, weightTotal, type CasStatus } from "./logic";
import type { ChemicalProfile, Ingredient } from "./types";

function CasBadge({ status }: { status: CasStatus }) {
  const { t } = useTranslation();
  if (status === "ok") return null;
  const cfg = status === "missing"
    ? { color: "warning", label: t("formulation.cas.missing", "缺 CAS"), tip: t("formulation.cas.missing_tip", "未识别 CAS，可在编辑模式下手动查询补全") }
    : status === "invalid"
      ? { color: "error", label: t("formulation.cas.invalid", "CAS 无效"), tip: t("formulation.cas.invalid_tip", "CAS 校验失败，后端 enrich 已忽略该值") }
      : { color: "cyan", label: t("formulation.cas.corrected", "已校正"), tip: t("formulation.cas.corrected_tip", "CAS 已与目录 / 检索结果对齐") };
  return <Tooltip title={cfg.tip}><Tag color={cfg.color} style={{ marginInlineStart: 4 }}>{cfg.label}</Tag></Tooltip>;
}

function ChemBadges({ profile }: { profile?: ChemicalProfile }) {
  const { t } = useTranslation();
  if (!profile) return null;
  const tags: { key: string; color: string; label: string; tip: string }[] = [];
  if (profile.patented === true) tags.push({ key: "p", color: "warning", label: t("formulation.chem.patented", "专利"), tip: t("formulation.chem.patented_tip", "分子结构已见于专利文献（molbloom 预筛），建议开展 FTO 检索") });
  if (profile.safety?.controlled === true) tags.push({ key: "c", color: "error", label: t("formulation.chem.controlled", "管制"), tip: t("formulation.chem.controlled_tip", "命中管制化学品清单，采购 / 使用需合规确认") });
  if (profile.safety?.explosive === true) tags.push({ key: "e", color: "error", label: t("formulation.chem.explosive", "爆炸性"), tip: t("formulation.chem.explosive_tip", "GHS 爆炸性危害标识") });
  for (const g of (profile.func_groups ?? []).slice(0, 3)) tags.push({ key: `g-${g}`, color: "default", label: g, tip: g });
  if (!tags.length) return null;
  return <div><Space wrap size={2}>{tags.map((b) => <Tooltip key={b.key} title={b.tip}><Tag color={b.color}>{b.label}</Tag></Tooltip>)}</Space></div>;
}

/** Ingredient table of a recommended formulation, optionally editable with CAS lookup on blur (legacy RecommendedFormulaTable). */
export function IngredientTable({ ingredients, editable, onChange, warnings = [] }: {
  ingredients: Ingredient[]; editable?: boolean; onChange?: (idx: number, patch: Partial<Ingredient>) => void; warnings?: string[];
}) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const [busy, setBusy] = useState<number | null>(null);
  // A profile describes a material, not a row position: shown only while the row still has the name it was resolved for.
  const [profiles, setProfiles] = useState<Record<number, { name: string; profile: ChemicalProfile }>>({});
  const current = useRef(ingredients);
  useEffect(() => { current.current = ingredients; }, [ingredients]);
  const roleLabel: Record<string, string> = {
    resin: t("formulation.role.resin", "树脂"), hardener: t("formulation.role.hardener", "固化剂"), inhibitor: t("formulation.role.inhibitor", "缓蚀剂"),
    solvent: t("formulation.role.solvent", "溶剂"), pigment: t("formulation.role.pigment", "颜料"), surfactant: t("formulation.role.surfactant", "表面活性剂"),
    additive: t("formulation.role.additive", "助剂"),
  };
  const edit = editable && !!onChange;

  async function lookup(idx: number, q: string) {
    if (!q.trim() || !onChange) return;
    const snapshot = ingredients;
    setBusy(idx);
    try {
      const hit = await fx.chemicalProfile(q.trim());
      if (current.current !== snapshot) return; // formulation replaced while waiting
      const name = hit.iupac_name || q;
      onChange(idx, { cas_no: hit.cas || undefined, name, zh_name: hit.zh_name || snapshot[idx]?.zh_name, formula: hit.formula || snapshot[idx]?.formula,
        molar_mass: hit.molar_mass ?? snapshot[idx]?.molar_mass, smiles: hit.smiles ?? snapshot[idx]?.smiles });
      setProfiles((p) => ({ ...p, [idx]: { name, profile: hit } }));
    } catch {
      if (current.current === snapshot) onChange(idx, { cas_no: q });
    } finally {
      setBusy(null);
    }
  }

  const total = weightTotal(ingredients);
  const dev = Math.abs(total - 100);
  const rows = ingredients.map((ing, idx) => ({ ing, idx }));
  return (
    <Table size="small" pagination={false} scroll={{ x: 900 }} dataSource={rows} rowKey="idx"
      locale={{ emptyText: t("formulation.ing.empty", "无组分数据") }}
      onRow={({ ing }) => ({ style: ing.grounding_confidence === "low" ? { background: token.colorWarningBg } : undefined })}
      columns={[
        { title: t("formulation.ing.type", "组分类型"), width: 100, render: (_d, { ing }) => ing.component_type || roleLabel[ing.role] || ing.role || t("formulation.ing.component", "组分") },
        { title: t("formulation.ing.zh", "中文名称"), width: 140, render: (_d, { ing, idx }) => edit
          ? <Input size="small" value={ing.zh_name ?? ""} onChange={(e) => onChange!(idx, { zh_name: e.target.value })} />
          : ing.zh_name || "—" },
        { title: t("formulation.ing.name", "成分"), width: 180, render: (_d, { ing, idx }) => edit
          ? <Input size="small" value={ing.name} onChange={(e) => onChange!(idx, { name: e.target.value })} />
          : ing.name },
        { title: "CAS No.", width: 160, render: (_d, { ing, idx }) => edit
          ? <Input size="small" value={ing.cas_no ?? ""} placeholder={busy === idx ? t("formulation.ing.looking", "查询中…") : "CAS"}
              onChange={(e) => onChange!(idx, { cas_no: e.target.value })} onBlur={(e) => void lookup(idx, e.target.value || ing.name)} />
          : <span><Typography.Text code>{ing.cas_no || "—"}</Typography.Text><CasBadge status={casStatus(ing, warnings)} /></span> },
        { title: t("formulation.ing.structure", "结构式"), width: 160, render: (_d, { ing, idx }) => <>
          {ing.smiles ? <Tooltip title={ing.smiles}><Typography.Text code>{ing.smiles.slice(0, 24)}{ing.smiles.length > 24 ? "…" : ""}</Typography.Text></Tooltip>
            : <Typography.Text type="secondary">{ing.mf_structure || ing.formula || "—"}</Typography.Text>}
          <ChemBadges profile={profiles[idx]?.name === ing.name ? profiles[idx].profile : undefined} />
        </> },
        { title: t("formulation.ing.mw", "分子量"), width: 90, align: "right", render: (_d, { ing }) => (ing.molar_mass != null ? ing.molar_mass.toFixed(2) : "—") },
        { title: t("formulation.ing.amount", "称重 / 体积"), width: 100, align: "right", render: (_d, { ing }) => ing.amount_display || `${ing.weight_pct}%` },
        { title: t("formulation.ing.notes", "备注"), render: (_d, { ing }) => <>
          {ing.notes || "—"}
          {ing.grounding_confidence === "low" && <Tooltip title={t("formulation.ing.low_tip", "缺少文献 / 专利依据，建议补充检索或手动核对")}><Tag color="warning" style={{ marginInlineStart: 4 }}>{t("formulation.ing.low", "低可信")}</Tag></Tooltip>}
          {(ing.evidence_refs?.length ?? 0) > 0 && <div><Typography.Text type="secondary" title={ing.evidence_refs!.join(", ")}>
            {t("formulation.ing.refs", "引用：{{r}}", { r: ing.evidence_refs!.slice(0, 2).join(", ") })}</Typography.Text></div>}
        </> },
      ]}
      summary={() => (
        <Table.Summary.Row>
          <Table.Summary.Cell index={0} colSpan={6} align="right">{t("formulation.ing.total", "合计")}</Table.Summary.Cell>
          <Table.Summary.Cell index={1} align="right">
            <Typography.Text strong={dev > 5} type={dev > 5 ? "danger" : dev > 0.5 ? "warning" : undefined}>{total.toFixed(1)}%</Typography.Text>
          </Table.Summary.Cell>
          <Table.Summary.Cell index={2} />
        </Table.Summary.Row>
      )}
    />
  );
}
