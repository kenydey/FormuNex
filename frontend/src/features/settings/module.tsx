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
      { path: "/admin/settings", key: "nav.admin.settings", zh: "设置中心", permission: "admin.llm", order: 3, desc: "模型、连接器与系统设置" },
      { path: "/admin/mcp-approvals", key: "nav.admin.mcp_approvals", zh: "MCP 工具审批", order: 0, desc: "审批 MCP 工具调用" },
      { path: "/admin/org-dashboard", key: "nav.admin.org_dashboard", zh: "组织看板", order: 5, desc: "组织级数据看板" },
    ],
  },
};

export default module;
