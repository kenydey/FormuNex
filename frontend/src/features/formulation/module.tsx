import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "lab/recommend", element: L(() => import("./RecommendPage")) },
    { path: "lab/leaderboard", element: L(() => import("./LeaderboardPage")) },
    { path: "lab/lineage", element: L(() => import("./LineagesPage")) },
    { path: "lab/lineage/:lineageId", element: L(() => import("./LineagePage")) },
    { path: "lab/provenance/:nodeType/:nodeId", element: L(() => import("./Provenance")) },
    { path: "lab/experiments", element: L(() => import("./ExperimentsPage")) },
    { path: "lab/experiments/:id", element: L(() => import("./ExperimentDetailPage")) },
    { path: "lab/experiments/workbench/:campaignId/rows/:rowId", element: L(() => import("./WorkbenchRowPage")) },
    { path: "lab/experiments/workbench/:campaignId/diff", element: L(() => import("./RowDiffPage")) },
  ],
  nav: {
    "/lab": [
      { path: "/lab/recommend", key: "nav.lab.recommend", zh: "AI 配方推荐" },
      { path: "/lab/leaderboard", key: "nav.lab.leaderboard", zh: "推荐排行榜" },
      { path: "/lab/lineage", key: "nav.lab.lineage", zh: "配方谱系" },
      { path: "/lab/experiments", key: "nav.lab.experiments", zh: "实验记录" },
    ],
  },
};

export default module;
