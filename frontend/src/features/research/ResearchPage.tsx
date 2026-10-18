import { PageContainer } from "@ant-design/pro-components";
import { Button, Space } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import DeepResearchTab from "./DeepResearchTab";
import ExternalSearchTab from "./ExternalSearchTab";
import ResearchChatTab from "./ResearchChatTab";
import StormReportTab from "./StormReportTab";
import { useResearch } from "./store";

const TABS = ["search", "chat", "deep", "storm"] as const;
type Tab = (typeof TABS)[number];

/** 外部检索与深度研究: federated external search → grounded Q&A → CRAG deep research report (legacy ResearchPanel). */
export default function ResearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw ?? "") ? (raw as Tab) : "search";
  const { sources, selected, report } = useResearch();
  return (
    <PageContainer
      subTitle={t("research.sub", "联邦检索专利 / 文献 / 网络，基于已选资料问答，并生成带引用的深度研究报告")}
      tabActiveKey={tab}
      onTabChange={(k) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", k); return n; }, { replace: true })}
      tabList={[
        { key: "search", tab: t("research.tab.search", "外部检索（{{n}}）", { n: sources.length }) },
        { key: "chat", tab: t("research.tab.chat", "资料问答（{{n}}）", { n: selected.length }) },
        { key: "deep", tab: report ? t("research.tab.deep_ready", "深度研究 · 已生成") : t("research.tab.deep", "深度研究") },
        { key: "storm", tab: t("research.tab.storm", "STORM 长文") },
      ]}
      extra={(
        <Space>
          <Button onClick={() => navigate("/knowledge/search")}>{t("research.link.kb_search", "内部知识库检索")}</Button>
          <Button onClick={() => navigate("/knowledge/research/artifacts")}>{t("research.link.artifacts", "研究产物")}</Button>
        </Space>
      )}
    >
      {tab === "search" && <ExternalSearchTab />}
      {tab === "chat" && <ResearchChatTab />}
      {tab === "deep" && <DeepResearchTab />}
      {tab === "storm" && <StormReportTab />}
    </PageContainer>
  );
}
