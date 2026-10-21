import { PageContainer } from "@ant-design/pro-components";
import { useTranslation } from "react-i18next";
import { LeversPanel } from "./LeversPanel";

/** Project DOE levers (legacy LeversEditor), shared by classic DOE, the closed loop and inverse design. */
export default function LeversPage() {
  const { t } = useTranslation();
  return (
    <PageContainer subTitle={t("design.levers.sub", "项目需求中的 DOE 因子：经典 DOE、闭环与 AI 选点共用")}>
      <LeversPanel />
    </PageContainer>
  );
}
