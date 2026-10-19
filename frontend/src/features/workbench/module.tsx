import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "cockpit/workbench", element: L(() => import("./WorkbenchPage")) },
    { path: "cockpit/workbench/:campaignId", element: L(() => import("./CampaignPage")) },
    { path: "cockpit/loop", element: L(() => import("./LoopPage")) },
    { path: "cockpit/models", element: L(() => import("./ModelsPage")) },
    { path: "cockpit/surface", element: L(() => import("./SurfacePage")) },
  ],
  nav: {
    "/lab": [
      { path: "/cockpit/workbench", key: "nav.cockpit.workbench", zh: "实验台账", order: 4 },
    ],
    "/cockpit": [
      { path: "/cockpit/loop", key: "nav.cockpit.loop", zh: "自驱动闭环", order: 7 },
      { path: "/cockpit/models", key: "nav.cockpit.models", zh: "代理模型", order: 8 },
      { path: "/cockpit/surface", key: "nav.cockpit.surface", zh: "响应曲面", order: 9 },
    ],
  },
};

export default module;
