import { PageContainer } from "@ant-design/pro-components";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Forbidden } from "@/shared/feedback";
import { useSession } from "@/app/session";
import { routePermissionFor } from "@/app/routePermissions";
import { normalizeSettingsTab, type SettingsTab } from "./logic";
import { ModelTab } from "./ModelTab";
import { ApiKeysTab } from "./ApiKeysTab";
import { ApiTokenPanel } from "./ApiTokenPanel";
import { SkillsTab } from "./SkillsTab";
import { EnvFlagsTab } from "./EnvFlagsTab";
import { DependenciesTab } from "./DependenciesTab";
import { KbMaintenanceTab } from "./KbMaintenanceTab";
import ConnectorsPage from "@/features/admin/ConnectorsPage";

/** Settings center opens for either admin role (W1-6/D2: OR gate; connectors tab keeps its own admin.connectors gate).
 * Stage 5 / SR-098: the OR list is derived from the routePermissions
 * table, not restated here — a second literal list would drift. */
export const SETTINGS_PERMISSIONS = routePermissionFor("/admin/settings")?.permissions ?? [];

/** 设置中心 /admin/settings/:tab — one routed tab per former settings-modal tab. */
export default function SettingsPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const { tab: raw } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const tab = normalizeSettingsTab(raw);
  if (raw !== tab) return <Navigate to={`/admin/settings/${tab}${location.search}`} replace />;
  if (!SETTINGS_PERMISSIONS.some((p) => can(p))) return <Forbidden permission={SETTINGS_PERMISSIONS.join(" / ")} />;

  const tabs: { key: SettingsTab; tab: string }[] = [
    { key: "model", tab: t("settings.tab.model", "模型") },
    { key: "keys", tab: t("settings.tab.keys", "API 密钥与检索路由") },
    { key: "api-token", tab: t("settings.tab.api_token", "API 访问令牌") },
    { key: "capabilities", tab: t("settings.tab.capabilities", "Skills 技能") },
    { key: "env", tab: t("settings.tab.env", "环境变量") },
    { key: "deps", tab: t("settings.tab.deps", "依赖管理") },
    { key: "kb", tab: t("settings.tab.kb", "KB / KG 维护") },
    { key: "connectors", tab: t("settings.tab.connectors", "连接器") },
  ];
  return (
    <PageContainer title={t("settings.title", "设置中心")} tabList={tabs} tabActiveKey={tab}
      onTabChange={(k) => navigate(`/admin/settings/${k}`)}>
      {tab === "connectors"
        ? (can("admin.connectors") ? <ConnectorsPage /> : <Forbidden permission="admin.connectors" />)
        // SR-098: every other tab writes through the admin.llm-guarded
        // settings APIs, so it gets the same second gate — an
        // admin.connectors-only user used to open each tab and meet a
        // 403 on every save button.
        : !can("admin.llm") ? <Forbidden permission="admin.llm" /> : (<>
          {tab === "model" && <ModelTab />}
          {tab === "keys" && <ApiKeysTab />}
          {tab === "api-token" && <ApiTokenPanel />}
          {tab === "capabilities" && <SkillsTab />}
          {tab === "env" && <EnvFlagsTab />}
          {tab === "deps" && <DependenciesTab />}
          {tab === "kb" && <KbMaintenanceTab />}
        </>)}
    </PageContainer>
  );
}
