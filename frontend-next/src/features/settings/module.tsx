import type { FeatureModule } from "@/app/modules";
import { Navigate } from "react-router-dom";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "admin/settings", element: <Navigate to="/admin/settings/model" replace /> },
    { path: "admin/settings/:tab", element: L(() => import("./SettingsPage")) },
    { path: "admin/mcp-approvals", element: L(() => import("./McpApprovalsPage")) },
    { path: "admin/org-dashboard", element: L(() => import("./OrgDashboardPage")) },
  ],
  nav: {
    "/admin": [
      { path: "/admin/settings", key: "nav.admin.settings", zh: "设置中心", permission: "admin.llm" },
      { path: "/admin/mcp-approvals", key: "nav.admin.mcp_approvals", zh: "MCP 工具审批" },
      { path: "/admin/org-dashboard", key: "nav.admin.org_dashboard", zh: "组织看板" },
    ],
  },
};

export default module;
