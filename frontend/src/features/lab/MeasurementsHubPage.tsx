import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useSession } from "@/app/session";
import { Forbidden } from "@/shared/feedback";
import { useHubTab } from "@/shared/hubTabs";
import QcReportPage from "../design/QcReportPage";
import ReviewQueuePage from "./ReviewQueuePage";

/**
 * 测量与复核 hub：检测报告解析 + 待复核队列（原 qc-report / review 两页）。
 * 复核 tab 只对有 ``lab.review`` 权限的用户可见；QC 的 ``sync_measured`` 自 P1-13 起只同步已复核值，
 * 新上传的 QC 值先进待复核队列，复核通过后才进入可训练数据。
 * 无权限用户直访 ?tab=review 时显式 403，不再静默回落到解析页（W1-7/P2-F5）。
 */
export default function MeasurementsHubPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const [params] = useSearchParams();
  const [tab, onChange] = useHubTab("tab", can("lab.review") ? ["parse", "review"] : ["parse"], "parse");
  if (params.get("tab") === "review" && !can("lab.review")) return <Forbidden permission="lab.review" />;
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "parse", label: t("hub.measurements.parse", "检测报告解析"), children: <QcReportPage /> },
      ...(can("lab.review") ? [{ key: "review", label: t("hub.measurements.review", "待复核队列"), children: <ReviewQueuePage /> }] : []),
    ]} />
  );
}
