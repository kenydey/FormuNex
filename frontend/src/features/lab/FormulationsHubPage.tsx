import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import FormulationEditorPage from "./FormulationEditorPage";
import LineagesPage from "../formulation/LineagesPage";

/** 配方 hub：配方编辑 + 配方谱系（原 formulations / lineage 两页；谱系详情路由不动）。 */
export default function FormulationsHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["editor", "lineages"], "editor");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "editor", label: t("hub.formulations.editor", "配方编辑"), children: <FormulationEditorPage /> },
      { key: "lineages", label: t("hub.formulations.lineages", "配方谱系"), children: <LineagesPage /> },
    ]} />
  );
}
