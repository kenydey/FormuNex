import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import SearchPage from "./SearchPage";
import DeepSearchPage from "./DeepSearchPage";
import OpsHubPage from "@/features/library/OpsHubPage";

/** 检索 hub：快速检索 + 深度检索（原 /knowledge/search 与 /knowledge/deep 两页）+ 检索运维（原 /knowledge/quality）。 */
export default function SearchHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["search", "deep", "ops"], "search");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "search", label: t("hub.search.quick", "快速检索"), children: <SearchPage /> },
      { key: "deep", label: t("hub.search.deep", "深度检索"), children: <DeepSearchPage /> },
      { key: "ops", label: t("hub.search.ops", "检索运维"), children: <OpsHubPage tabParam="opstab" /> },
    ]} />
  );
}
