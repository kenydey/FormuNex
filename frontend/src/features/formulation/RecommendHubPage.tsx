import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import RecommendPage from "./RecommendPage";
import LeaderboardPage from "./LeaderboardPage";

/** AI 推荐 hub：生成推荐 + 排行榜（原 recommend / leaderboard 两页）。生成完成后推荐页自动切排行榜 tab。 */
export default function RecommendHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["generate", "board"], "generate");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "generate", label: t("hub.recommend.generate", "生成推荐"), children: <RecommendPage /> },
      { key: "board", label: t("hub.recommend.board", "排行榜"), children: <LeaderboardPage /> },
    ]} />
  );
}
