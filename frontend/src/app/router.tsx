import { Suspense, type ReactNode } from "react";
import { Navigate, createBrowserRouter, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, Result } from "antd";
import { useSession } from "./session";
import { errorText } from "@/shared/feedback";
import { Shell } from "./Shell";
import { PageSpin } from "@/shared/PageSpin";
import { Forbidden } from "@/shared/feedback";
import { TabRedirect } from "@/shared/hubTabs";
import { canAccessRoute, routePermissionFor } from "./routePermissions";
import LoginPage from "@/features/auth/LoginPage";
import { MODULES } from "./modules";
import { needsSetup, useDesktop } from "@/desktop/runtime";
import { L } from "./lazy";

function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { me, ready, error, logout, reload } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const desktopInfo = useDesktop((s) => s.info);
  if (needsSetup(desktopInfo)) return <Navigate to="/setup" replace />;
  if (!ready) return <PageSpin />;
  // SR-075: a non-401 session failure is an error page, not a bounce to
  // /login — the token may be fine and the login page would only mislead.
  if (error) {
    return (
      <Result
        status="error"
        title={t("session.load_failed", "会话加载失败")}
        subTitle={errorText(error)}
        extra={[
          <Button key="retry" type="primary" onClick={() => void reload()}>{t("common.retry", "重试")}</Button>,
          <Button key="logout" onClick={() => void logout().then(() => navigate("/login"))}>{t("session.logout", "退出登录")}</Button>,
        ]}
      />
    );
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

/**
 * Stage 4 / SR-056: route-level permission interception. Runs inside
 * RequireAuth (login already established), consults the single
 * routePermissions table, and renders a bare <Forbidden> for direct URL
 * visits without the permission. Page-level <Forbidden> checks stay as
 * defense in depth; redirects are guarded again after they land.
 */
function RequirePermission({ children }: { children: ReactNode }) {
  const { can } = useSession();
  const location = useLocation();
  if (!canAccessRoute(location.pathname, can)) {
    const perm = routePermissionFor(location.pathname);
    const label = perm?.permission ?? (perm?.permissions ?? []).join(" / ");
    return <Forbidden permission={label} />;
  }
  return <>{children}</>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/setup", element: <Suspense fallback={<PageSpin />}>{L(() => import("@/features/desktop/SetupPage"))}</Suspense> },
  {
    path: "/",
    element: <RequireAuth><RequirePermission><Shell /></RequirePermission></RequireAuth>,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: L(() => import("@/features/dashboard/DashboardPage")) },
      { path: "knowledge/search", element: L(() => import("@/features/knowledge/SearchHubPage")) },
      { path: "knowledge/deep", element: <TabRedirect to="/knowledge/search" tab="deep" /> },
      { path: "knowledge/sources", element: L(() => import("@/features/knowledge/SourcesPage")) },
      { path: "knowledge/sources/:sourceId", element: L(() => import("@/features/knowledge/SourceDetailPage")) },
      { path: "spec/projects", element: L(() => import("@/features/spec/ProjectsPage")) },
      { path: "spec/projects/:projectId", element: L(() => import("@/features/spec/SpecStudioPage")) },
      { path: "lab/formulations", element: L(() => import("@/features/lab/FormulationsHubPage")) },
      { path: "lab/doe", element: L(() => import("@/features/lab/DoeHubPage")) },
      { path: "lab/tasks", element: <TabRedirect to="/lab/work-records" tab="tasks" /> },
      { path: "lab/tasks/:taskId", element: L(() => import("@/features/lab/TaskDetailPage")) },
      { path: "lab/review", element: <TabRedirect to="/lab/measurements" tab="review" /> },
      { path: "lab/measurements", element: L(() => import("@/features/lab/MeasurementsHubPage")) },
      { path: "lab/methods", element: L(() => import("@/features/lab/MethodsPage")) },
      { path: "lab/eln", element: L(() => import("@/features/lab/ElnPage")) },
      { path: "cockpit/pareto", element: <TabRedirect to="/cockpit/optimization" tab="pareto" /> },
      { path: "cockpit/conformal", element: <TabRedirect to="/cockpit/uncertainty" tab="conformal" /> },
      { path: "cockpit/acquisition", element: <TabRedirect to="/cockpit/uncertainty" tab="acquisition" /> },
      { path: "cockpit/point-review", element: <TabRedirect to="/cockpit/optimization" tab="review" /> },
      { path: "cockpit/rheology", element: L(() => import("@/features/cockpit/RheologyPage")) },
      { path: "cockpit/color", element: <TabRedirect to="/cockpit/properties" tab="color" /> },
      { path: "cockpit/acid-stability", element: <TabRedirect to="/cockpit/properties" tab="acid" /> },
      { path: "cockpit/uncertainty", element: L(() => import("@/features/cockpit/UncertaintyHubPage")) },
      { path: "cockpit/properties", element: L(() => import("@/features/cockpit/PropertiesHubPage")) },
      { path: "plm/products", element: L(() => import("@/features/plm/ProductsPage")) },
      { path: "plm/formula-mappings", element: L(() => import("@/features/plm/FormulaMappingsPage")) },
      { path: "plm/products/:productId", element: L(() => import("@/features/plm/ProductDetailPage")) },
      { path: "plm/revisions/:revisionId", element: L(() => import("@/features/plm/RevisionPage")) },
      { path: "plm/documents/:documentId", element: L(() => import("@/features/plm/DocumentPage")) },
      { path: "plm/documents/:documentId/proofread", element: L(() => import("@/features/plm/DocumentProofreadPage")) },
      { path: "plm/packages/:packageId", element: L(() => import("@/features/plm/PackagePage")) },
      { path: "plm/label-designer", element: L(() => import("@/features/plm/LabelDesignerPage")) },
      { path: "plm/regulatory", element: L(() => import("@/features/regulatory/RegulatoryAgentPage")) },
      { path: "plm/regulatory/review-items", element: L(() => import("@/features/regulatory/ReviewItemsPage")) },
      { path: "materials/substances", element: L(() => import("@/features/materials/SubstancesPage")) },
      { path: "materials/test-records", element: L(() => import("@/features/materials/TestRecordsPage")) },
      { path: "materials/raw", element: L(() => import("@/features/materials/MaterialsPage")) },
      { path: "admin/users", element: L(() => import("@/features/admin/UsersPage")) },
      { path: "admin/companies", element: L(() => import("@/features/admin/CompaniesPage")) },
      { path: "admin/audit", element: L(() => import("@/features/admin/AuditPage")) },
      { path: "admin/connectors", element: <Navigate to="/admin/settings/connectors" replace /> },
      ...MODULES.flatMap((m) => m.routes),
      { path: "*", element: L(() => import("@/features/dashboard/NotFoundPage")) },
    ],
  },
]);
