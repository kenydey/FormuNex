import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useSession } from "@/app/session";
import { useHubTab } from "@/shared/hubTabs";
import QcReportPage from "../design/QcReportPage";
import ReviewQueuePage from "./ReviewQueuePage";

/**
 * 测量与复核 hub：检测报告解析 + 待复核队列（原 qc-report / review 两页）。
 * 复核 tab 只对有 ``lab.review`` 权限的用户可见；QC 的 ``sync_measured`` 仍直接同步进可训练数据，
 * 不经过复核队列——语义不变，不伪装成已复核。
 */
export default function MeasurementsHubPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const [tab, onChange] = useHubTab("tab", can("lab.review") ? ["parse", "review"] : ["parse"], "parse");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "parse", label: t("hub.measurements.parse", "检测报告解析"), children: <QcReportPage /> },
      ...(can("lab.review") ? [{ key: "review", label: t("hub.measurements.review", "待复核队列"), children: <ReviewQueuePage /> }] : []),
    ]} />
  );
}
