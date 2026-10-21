import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import SearchPage from "./SearchPage";
import DeepSearchPage from "./DeepSearchPage";

/** 检索 hub：快速检索 + 深度检索（原 /knowledge/search 与 /knowledge/deep 两页）。 */
export default function SearchHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["search", "deep"], "search");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "search", label: t("hub.search.quick", "快速检索"), children: <SearchPage /> },
      { key: "deep", label: t("hub.search.deep", "深度检索"), children: <DeepSearchPage /> },
    ]} />
  );
}
