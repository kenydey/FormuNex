import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import DoeDesignerPage from "./DoeDesignerPage";
import ClassicDoePage from "../design/ClassicDoePage";
import LeversPage from "../design/LeversPage";
import DoeHistoryPage from "../design/DoeHistoryPage";

/** DOE hub：设计器 + 经典与闭环 + 因子 + 历史（原 doe / doe/classic / doe/levers / doe/history 四页）。 */
export default function DoeHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["designer", "classic", "levers", "history"], "designer");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "designer", label: t("hub.doe.designer", "设计器"), children: <DoeDesignerPage /> },
      { key: "classic", label: t("hub.doe.classic", "经典与闭环"), children: <ClassicDoePage /> },
      { key: "levers", label: t("hub.doe.levers", "因子"), children: <LeversPage /> },
      { key: "history", label: t("hub.doe.history", "历史"), children: <DoeHistoryPage /> },
    ]} />
  );
}
