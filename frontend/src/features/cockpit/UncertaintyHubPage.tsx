import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import ConformalPage from "./ConformalPage";
import AcquisitionTrendPage from "./AcquisitionTrendPage";

/** 不确定性诊断 hub：预测区间与偏差 + 采集函数趋势（原 conformal / acquisition 两页）。 */
export default function UncertaintyHubPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["conformal", "acquisition"], "conformal");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "conformal", label: t("hub.uncertainty.conformal", "预测区间与偏差"), children: <ConformalPage /> },
      { key: "acquisition", label: t("hub.uncertainty.acquisition", "采集函数趋势"), children: <AcquisitionTrendPage /> },
    ]} />
  );
}
