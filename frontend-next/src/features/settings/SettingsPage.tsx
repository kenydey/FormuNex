import { PageContainer } from "@ant-design/pro-components";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Forbidden } from "@/shared/feedback";
import { useSession } from "@/app/session";
import { normalizeSettingsTab, type SettingsTab } from "./logic";
import { ModelTab } from "./ModelTab";
import { ApiKeysTab } from "./ApiKeysTab";
import { SkillsTab } from "./SkillsTab";
import { EnvFlagsTab } from "./EnvFlagsTab";
import { DependenciesTab } from "./DependenciesTab";
import { KbMaintenanceTab } from "./KbMaintenanceTab";

/** Server configuration is a system-administrator task (rbac: admin.llm). */
export const SETTINGS_PERMISSION = "admin.llm";

/** 设置中心 /admin/settings/:tab — one routed tab per former settings-modal tab. */
export default function SettingsPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const { tab: raw } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const tab = normalizeSettingsTab(raw);
  if (raw !== tab) return <Navigate to={`/admin/settings/${tab}${location.search}`} replace />;
  if (!can(SETTINGS_PERMISSION)) return <Forbidden permission={SETTINGS_PERMISSION} />;

  const tabs: { key: SettingsTab; tab: string }[] = [
    { key: "model", tab: t("settings.tab.model", "模型") },
    { key: "keys", tab: t("settings.tab.keys", "API 密钥与检索路由") },
    { key: "capabilities", tab: t("settings.tab.capabilities", "Skills 技能") },
    { key: "env", tab: t("settings.tab.env", "环境变量") },
    { key: "deps", tab: t("settings.tab.deps", "依赖管理") },
    { key: "kb", tab: t("settings.tab.kb", "KB / KG 维护") },
  ];
  return (
    <PageContainer title={t("settings.title", "设置中心")} tabList={tabs} tabActiveKey={tab}
      onTabChange={(k) => navigate(`/admin/settings/${k}`)}>
      {tab === "model" && <ModelTab />}
      {tab === "keys" && <ApiKeysTab />}
      {tab === "capabilities" && <SkillsTab />}
      {tab === "env" && <EnvFlagsTab />}
      {tab === "deps" && <DependenciesTab />}
      {tab === "kb" && <KbMaintenanceTab />}
    </PageContainer>
  );
}
