import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// Menu round 2: the ledger list merges into the work-records hub (detail route stays); loop merges
// into the optimization hub; models / surface stay standalone. No nav entries left here — the
// cockpit leaves live in app/nav.tsx BASE_NAV, the lab leaves in the formulation module.
const module: FeatureModule = {
  routes: [
    { path: "cockpit/workbench", element: <TabRedirect to="/lab/work-records" tab="ledger" /> },
    { path: "cockpit/workbench/:campaignId", element: L(() => import("./CampaignPage")) },
    { path: "cockpit/loop", element: <TabRedirect to="/cockpit/optimization" tab="loop" /> },
    { path: "cockpit/optimization", element: L(() => import("./OptimizationHubPage")) },
    { path: "cockpit/models", element: L(() => import("./ModelsPage")) },
    { path: "cockpit/surface", element: L(() => import("./SurfacePage")) },
  ],
  nav: {},
};

export default module;
