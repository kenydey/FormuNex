import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter, useLocation } from "react-router-dom";
import { useSession } from "./session";
import { Shell } from "./Shell";
import { PageSpin } from "@/shared/PageSpin";
import { TabRedirect } from "@/shared/hubTabs";
import LoginPage from "@/features/auth/LoginPage";
import { MODULES } from "./modules";
import { needsSetup, useDesktop } from "@/desktop/runtime";

const L = (f: () => Promise<{ default: React.ComponentType }>) => {
  const C = lazy(f);
  return <C />;
};

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, ready } = useSession();
  const location = useLocation();
  const desktopInfo = useDesktop((s) => s.info);
  if (needsSetup(desktopInfo)) return <Navigate to="/setup" replace />;
  if (!ready) return <PageSpin />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/setup", element: <Suspense fallback={<PageSpin />}>{L(() => import("@/features/desktop/SetupPage"))}</Suspense> },
  {
    path: "/",
    element: <RequireAuth><Shell /></RequireAuth>,
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
