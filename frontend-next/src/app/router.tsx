import { lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter, useLocation } from "react-router-dom";
import { useSession } from "./session";
import { Shell } from "./Shell";
import { PageSpin } from "@/shared/PageSpin";
import LoginPage from "@/features/auth/LoginPage";
import { MODULES } from "./modules";

const L = (f: () => Promise<{ default: React.ComponentType }>) => {
  const C = lazy(f);
  return <C />;
};

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, ready } = useSession();
  const location = useLocation();
  if (!ready) return <PageSpin />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <RequireAuth><Shell /></RequireAuth>,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: L(() => import("@/features/dashboard/DashboardPage")) },
      { path: "knowledge/search", element: L(() => import("@/features/knowledge/SearchPage")) },
      { path: "knowledge/deep", element: L(() => import("@/features/knowledge/DeepSearchPage")) },
      { path: "knowledge/sources", element: L(() => import("@/features/knowledge/SourcesPage")) },
      { path: "knowledge/sources/:sourceId", element: L(() => import("@/features/knowledge/SourceDetailPage")) },
      { path: "spec/projects", element: L(() => import("@/features/spec/ProjectsPage")) },
      { path: "spec/projects/:projectId", element: L(() => import("@/features/spec/SpecStudioPage")) },
      { path: "lab/formulations", element: L(() => import("@/features/lab/FormulationEditorPage")) },
      { path: "lab/doe", element: L(() => import("@/features/lab/DoeDesignerPage")) },
      { path: "lab/tasks", element: L(() => import("@/features/lab/TasksPage")) },
      { path: "lab/tasks/:taskId", element: L(() => import("@/features/lab/TaskDetailPage")) },
      { path: "lab/review", element: L(() => import("@/features/lab/ReviewQueuePage")) },
      { path: "lab/methods", element: L(() => import("@/features/lab/MethodsPage")) },
      { path: "lab/eln", element: L(() => import("@/features/lab/ElnPage")) },
      { path: "cockpit/pareto", element: L(() => import("@/features/cockpit/ParetoPage")) },
      { path: "cockpit/conformal", element: L(() => import("@/features/cockpit/ConformalPage")) },
      { path: "plm/products", element: L(() => import("@/features/plm/ProductsPage")) },
      { path: "plm/products/:productId", element: L(() => import("@/features/plm/ProductDetailPage")) },
      { path: "plm/revisions/:revisionId", element: L(() => import("@/features/plm/RevisionPage")) },
      { path: "plm/documents/:documentId", element: L(() => import("@/features/plm/DocumentPage")) },
      { path: "plm/packages/:packageId", element: L(() => import("@/features/plm/PackagePage")) },
      { path: "plm/label-designer", element: L(() => import("@/features/plm/LabelDesignerPage")) },
      { path: "materials/substances", element: L(() => import("@/features/materials/SubstancesPage")) },
      { path: "materials/raw", element: L(() => import("@/features/materials/MaterialsPage")) },
      { path: "admin/users", element: L(() => import("@/features/admin/UsersPage")) },
      { path: "admin/audit", element: L(() => import("@/features/admin/AuditPage")) },
      { path: "admin/connectors", element: L(() => import("@/features/admin/ConnectorsPage")) },
      ...MODULES.flatMap((m) => m.routes),
      { path: "*", element: L(() => import("@/features/dashboard/NotFoundPage")) },
    ],
  },
]);
