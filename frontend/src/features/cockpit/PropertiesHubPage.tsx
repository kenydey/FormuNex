import { useState } from "react";
import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import ColorPage from "./ColorPage";
import AcidStabilityPage from "./AcidStabilityPage";
import type { DraftFormulation } from "./FormulationEditor";

/**
 * 配方物性 hub：色度 + 酸稳定性（原 color / acid-stability 两页）。
 * 两页共用同一组草稿配方：编辑器状态提升到 hub 级，色度 tab 的默认种子即 hub 初始值
 * （原酸稳定性页的 Zinc dust 种子不再单独出现——共享状态的必然结果，非功能删减）。
 */
const INITIAL_FORMS: DraftFormulation[] = [
  { name: "配方 1", ingredients: [{ name: "Titanium dioxide", weight_pct: 20, role: "pigment" }] },
];

export default function PropertiesHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["color", "acid"], "color");
  const [forms, setForms] = useState<DraftFormulation[]>(INITIAL_FORMS);
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "color", label: t("hub.properties.color", "色度"), children: <ColorPage forms={forms} onFormsChange={setForms} /> },
      { key: "acid", label: t("hub.properties.acid", "酸稳定性"), children: <AcidStabilityPage forms={forms} onFormsChange={setForms} /> },
    ]} />
  );
}
