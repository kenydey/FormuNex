import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useSession } from "@/app/session";
import { Forbidden } from "@/shared/feedback";
import { useHubTab } from "@/shared/hubTabs";
import SearchPage from "./SearchPage";
import DeepSearchPage from "./DeepSearchPage";
import OpsHubPage from "@/features/library/OpsHubPage";

/** 检索 hub：快速检索 + 深度检索（原 /knowledge/search 与 /knowledge/deep 两页）+ 检索运维（原 /knowledge/quality）。
 * 快速/深度检索走 /api/search 与 /api/research，后端要求 knowledge.read（W1-6/P2-F9/D7）；无权限时显式 403，
 * 运维 tab 不走这两条入口、保持原样。 */
export default function SearchHubPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const [tab, onChange] = useHubTab("tab", ["search", "deep", "ops"], "search");
  const gated = can("knowledge.read");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "search", label: t("hub.search.quick", "快速检索"), children: gated ? <SearchPage /> : <Forbidden permission="knowledge.read" /> },
      { key: "deep", label: t("hub.search.deep", "深度检索"), children: gated ? <DeepSearchPage /> : <Forbidden permission="knowledge.read" /> },
      { key: "ops", label: t("hub.search.ops", "检索运维"), children: <OpsHubPage tabParam="opstab" /> },
    ]} />
  );
}
