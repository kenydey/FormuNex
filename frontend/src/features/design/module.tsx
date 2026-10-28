import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// Menu round 2: classic / history / levers merge into the DOE hub; similar merges into the
// work-records hub; qc-report merges into the measurements hub.
const module: FeatureModule = {
  routes: [
    { path: "lab/doe/classic", element: <TabRedirect to="/lab/doe" tab="classic" /> },
    { path: "lab/doe/history", element: <TabRedirect to="/lab/doe" tab="history" /> },
    { path: "lab/doe/levers", element: <TabRedirect to="/lab/doe" tab="levers" /> },
    { path: "lab/inverse-design", element: L(() => import("./InverseDesignPage")) },
    { path: "lab/substitution", element: L(() => import("./SubstitutionPage")) },
    { path: "lab/similar", element: <TabRedirect to="/lab/work-records" tab="similar" /> },
    { path: "lab/qc-report", element: <TabRedirect to="/lab/measurements" tab="parse" /> },
  ],
  nav: {
    "/lab": [
      { path: "/lab/inverse-design", key: "nav.lab.inverse", zh: "逆向设计", order: 6, desc: "给定目标性能反向搜索配方" },
      { path: "/lab/substitution", key: "nav.lab.substitution", zh: "原料替代", order: 5, desc: "找可替代原料并评估影响" },
    ],
  },
};

export default module;
