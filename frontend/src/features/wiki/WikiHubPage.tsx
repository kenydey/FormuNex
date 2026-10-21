import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import WikiPage from "./WikiPage";
import WikiGraphPage from "./WikiGraphPage";

/**
 * Wiki 知识库 hub：页面浏览与阅读 + 链接图（原 wiki 与 wiki-graph 两页）。
 * ``knowledge/wiki/*`` 通配路由不动：点开具体 Wiki 页仍走原 WikiPage 路由。
 * 图 tab 的 ``wiki_page_graph_enabled`` 旗标门禁活在图页内部，原样保留。
 */
export default function WikiHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("view", ["pages", "graph"], "pages");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "pages", label: t("hub.wiki.pages", "页面浏览与阅读"), children: <WikiPage /> },
      { key: "graph", label: t("hub.wiki.graph", "链接图"), children: <WikiGraphPage /> },
    ]} />
  );
}
