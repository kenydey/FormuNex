import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useHubTab } from "@/shared/hubTabs";
import TasksPage from "../lab/TasksPage";
import WorkbenchPage from "../workbench/WorkbenchPage";
import ExperimentsPage from "./ExperimentsPage";
import SimilarPage from "../design/SimilarPage";

/**
 * 实验工作记录 hub：实验任务 + 实验台账 + 训练实验记录 + 跨批次检索 + 相似历史配方。
 * 实验记录页自身用 ``?tab=`` 分 records/search 两 tab，与 hub 的 ``?tab=`` 冲突，故 hub 以 prop
 * 强制指定其 tab 并隐藏它自己的 tab 条；详情路由（任务详情、台账详情、实验详情）全部保留。
 */
export default function WorkRecordsPage() {
  const { t } = useTranslation();
  const [tab, onChange] = useHubTab("tab", ["tasks", "ledger", "records", "search", "similar"], "tasks");
  return (
    <Tabs activeKey={tab} onChange={onChange} items={[
      { key: "tasks", label: t("hub.work.tasks", "实验任务"), children: <TasksPage /> },
      { key: "ledger", label: t("hub.work.ledger", "实验台账"), children: <WorkbenchPage /> },
      { key: "records", label: t("hub.work.records", "训练实验记录"), children: <ExperimentsPage tab="records" tabs={false} /> },
      { key: "search", label: t("hub.work.search", "跨批次检索"), children: <ExperimentsPage tab="search" tabs={false} /> },
      { key: "similar", label: t("hub.work.similar", "相似历史配方"), children: <SimilarPage /> },
    ]} />
  );
}
