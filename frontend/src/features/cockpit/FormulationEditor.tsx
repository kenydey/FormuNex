import { Button, Input, InputNumber, Select, Space } from "antd";
import { useTranslation } from "react-i18next";

export interface DraftIngredient {
  name: string;
  weight_pct: number;
  role: string;
}

export interface DraftFormulation {
  name: string;
  ingredients: DraftIngredient[];
}

/** 补齐 2-21: 色度/酸稳定性共用的配方编辑器（组分行：名称 + 占比 + 角色）。 */
export function FormulationEditor({ formulations, onChange }: {
  formulations: DraftFormulation[];
  onChange: (v: DraftFormulation[]) => void;
}) {
  const { t } = useTranslation();
  const roles = ["pigment", "resin", "solvent", "filler", "additive", "hardener", "inhibitor"];
  const set = (i: number, f: DraftFormulation) => onChange(formulations.map((x, j) => (j === i ? f : x)));
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {formulations.map((form, i) => (
        <div key={i} style={{ border: "1px solid #d9d9d9", borderRadius: 8, padding: 12 }}>
          <Space wrap>
            <Input style={{ width: 200 }} placeholder={t("cockpit.form_name", "配方名")} value={form.name}
              onChange={(e) => set(i, { ...form, name: e.target.value })} />
            <Button danger size="small" onClick={() => onChange(formulations.filter((_, j) => j !== i))}>{t("cockpit.form_delete", "删除配方")}</Button>
          </Space>
          <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
            {form.ingredients.map((ing, j) => (
              <Space key={j} wrap>
                <Input style={{ width: 220 }} placeholder={t("cockpit.ing_name", "组分名，如 Titanium dioxide")} value={ing.name}
                  onChange={(e) => set(i, { ...form, ingredients: form.ingredients.map((x, k) => (k === j ? { ...x, name: e.target.value } : x)) })} />
                <InputNumber style={{ width: 130 }} min={0} max={100} placeholder={t("cockpit.ing_pct", "占比 %")} value={ing.weight_pct}
                  onChange={(v) => set(i, { ...form, ingredients: form.ingredients.map((x, k) => (k === j ? { ...x, weight_pct: v ?? 0 } : x)) })} />
                <Select style={{ width: 140 }} value={ing.role}
                  options={roles.map((r) => ({ value: r, label: r }))}
                  onChange={(v) => set(i, { ...form, ingredients: form.ingredients.map((x, k) => (k === j ? { ...x, role: v } : x)) })} />
                <Button danger size="small" onClick={() => set(i, { ...form, ingredients: form.ingredients.filter((_, k) => k !== j) })}>{t("cockpit.ing_delete", "删除")}</Button>
              </Space>
            ))}
            <Button size="small" onClick={() => set(i, { ...form, ingredients: [...form.ingredients, { name: "", weight_pct: 0, role: "pigment" }] })}>{t("cockpit.ing_add", "添加组分")}</Button>
          </Space>
        </div>
      ))}
      <Button onClick={() => onChange([...formulations, { name: `配方 ${formulations.length + 1}`, ingredients: [{ name: "", weight_pct: 0, role: "pigment" }] }])}>{t("cockpit.form_add", "添加配方")}</Button>
    </Space>
  );
}
