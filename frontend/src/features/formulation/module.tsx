import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// Menu round 2: recommend + leaderboard merge into the recommend hub; experiments + tasks + ledger +
// similar merge into the work-records hub (route here); lineage list merges into the formulations hub.
const module: FeatureModule = {
  routes: [
    { path: "lab/recommend", element: L(() => import("./RecommendHubPage")) },
    { path: "lab/leaderboard", element: <TabRedirect to="/lab/recommend" tab="board" /> },
    { path: "lab/lineage", element: <TabRedirect to="/lab/formulations" tab="lineages" /> },
    { path: "lab/lineage/:lineageId", element: L(() => import("./LineagePage")) },
    { path: "lab/provenance/:nodeType/:nodeId", element: L(() => import("./Provenance")) },
    { path: "lab/work-records", element: L(() => import("./WorkRecordsPage")) },
    { path: "lab/experiments", element: <TabRedirect to="/lab/work-records" tab="records" /> },
    { path: "lab/experiments/:id", element: L(() => import("./ExperimentDetailPage")) },
    { path: "lab/experiments/workbench/:campaignId/rows/:rowId", element: L(() => import("./WorkbenchRowPage")) },
    { path: "lab/experiments/workbench/:campaignId/diff", element: L(() => import("./RowDiffPage")) },
  ],
  nav: {
    "/lab": [
      { path: "/lab/work-records", key: "nav.lab.work_records", zh: "实验工作记录", order: 2 },
      { path: "/lab/recommend", key: "nav.lab.recommend", zh: "AI 配方推荐", order: 6 },
    ],
  },
};

export default module;
