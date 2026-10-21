import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import PointReviewPage from "../cockpit/PointReviewPage";
import LoopPage from "./LoopPage";
import ParetoPage from "../cockpit/ParetoPage";

/**
 * 寻优闭环 hub：逐点审核 + 闭环迭代 + 帕累托与定型（原 point-review / loop / pareto 三页），
 * 按 workflow 顺序排列，默认 tab 为帕累托与定型。
 * 闭环页的运行态活在 zustand store（workbench/loop.ts）中，组件卸载不丢失；antd Tabs 默认不销毁
 * 非活动面板，故切到别的 tab 后 5 秒轮询/SSE 仍在后台继续。
 */
export default function OptimizationHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["review", "loop", "pareto"], "pareto");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "review", label: t("hub.optimization.review", "逐点审核"), children: <PointReviewPage /> },
      { key: "loop", label: t("hub.optimization.loop", "闭环迭代"), children: <LoopPage /> },
      { key: "pareto", label: t("hub.optimization.pareto", "帕累托与定型"), children: <ParetoPage /> },
    ]} />
  );
}
