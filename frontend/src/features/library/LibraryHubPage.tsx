import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import LiteratureLibraryPage from "./LiteratureLibraryPage";
import ManifestPage from "./ManifestPage";
import CollectionsPage from "./CollectionsPage";
import NotebookLMPage from "./NotebookLMPage";

/**
 * 文献库 hub：目录与导入 + 冻结与筛选 + 智能集合（原 library / manifest / collections 三页）
 * + NotebookLM（原系统管理组菜单项）。
 * 门禁说明：``literature_library_enabled`` 只活在文献库页内部，只限制"目录与导入" tab 的内容，
 * 冻结与筛选 tab 不加门（原冻结页本无此门禁）。
 */
export default function LibraryHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["catalog", "frozen", "smart", "notebooklm"], "catalog");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "catalog", label: t("hub.library.catalog", "目录与导入"), children: <LiteratureLibraryPage /> },
      { key: "frozen", label: t("hub.library.frozen", "冻结与筛选"), children: <ManifestPage /> },
      // 与文献库内的"手动集合"同名不同物：tab 文案必须带"（定时规则）"以区分。
      { key: "smart", label: t("hub.library.smart", "智能集合（定时规则）"), children: <CollectionsPage /> },
      { key: "notebooklm", label: t("hub.library.notebooklm", "NotebookLM"), children: <NotebookLMPage /> },
    ]} />
  );
}
