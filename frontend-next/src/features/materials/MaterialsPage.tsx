import { PageContainer } from "@ant-design/pro-components";
import { Badge, Space } from "antd";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CandidatesTab, useCandidates } from "@/features/requirements/materials/CandidatesTab";
import { MaterialLibraryTab } from "@/features/requirements/materials/MaterialLibraryTab";
import { StructureSearchTab } from "@/features/requirements/materials/StructureSearchTab";

const TABS = ["library", "pending", "structure"] as const;
type Tab = (typeof TABS)[number];

/** 原料库 (legacy MaterialsPanel): catalogue, pending candidates, structure search — the tab is in the URL (?tab=). */
export default function MaterialsPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw ?? "") ? (raw as Tab) : "library";
  const candidates = useCandidates();
  const pending = candidates.data?.total ?? candidates.data?.candidates.length ?? 0;
  return (
    <PageContainer
      subTitle={t("requirements.mat.page_sub", "原料主数据：供应状态驱动替代推荐，SMILES / 分子量供结构检索与配方计算")}
      tabActiveKey={tab}
      onTabChange={(k) => setParams(k === "library" ? {} : { tab: k }, { replace: true })}
      tabList={[
        { key: "library", tab: t("requirements.mat.tab_library", "材料列表") },
        { key: "pending", tab: <Space size={4}>{t("requirements.mat.tab_pending", "待入库")}<Badge count={pending} size="small" overflowCount={999} /></Space> },
        { key: "structure", tab: t("requirements.mat.tab_structure", "结构搜索") },
      ]}>
      {tab === "library" && <MaterialLibraryTab />}
      {tab === "pending" && <CandidatesTab />}
      {tab === "structure" && <StructureSearchTab />}
    </PageContainer>
  );
}
