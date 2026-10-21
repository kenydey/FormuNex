import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import QualityPage from "./QualityPage";
import RetrievalProbePage from "./RetrievalProbePage";
import ParsingPage from "./ParsingPage";

/** 检索质量运维 hub：质量看板 + 检索探针 + 解析与结构识别（原 quality / retrieval / parsing 三页）。 */
export default function OpsHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["quality", "probe", "parsing"], "quality");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "quality", label: t("hub.ops.quality", "质量看板"), children: <QualityPage /> },
      { key: "probe", label: t("hub.ops.probe", "检索探针"), children: <RetrievalProbePage /> },
      { key: "parsing", label: t("hub.ops.parsing", "解析与结构识别"), children: <ParsingPage /> },
    ]} />
  );
}
