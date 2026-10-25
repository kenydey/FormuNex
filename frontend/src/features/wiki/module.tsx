import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    // ``knowledge/wiki/<page path>`` opens a page (e.g. /knowledge/wiki/materials/e51.md); [[wikilinks]] link here.
    // The hub route (exact /knowledge/wiki) outranks the splat, so the hub renders for the bare path only.
    { path: "knowledge/wiki", element: L(() => import("./WikiHubPage")) },
    { path: "knowledge/wiki/*", element: L(() => import("./WikiPage")) },
    { path: "knowledge/wiki-graph", element: <TabRedirect to="/knowledge/wiki" tab="graph" param="view" /> },
    { path: "knowledge/graph", element: L(() => import("./KgGraphPage")) },
  ],
  nav: {
    "/knowledge-library": [
      { path: "/knowledge/wiki", key: "nav.knowledge.wiki", zh: "Wiki 知识库", order: 5 },
    ],
    "/materials": [
      { path: "/knowledge/graph", key: "nav.knowledge.graph", zh: "材料知识图谱", order: 3 },
    ],
  },
};

export default module;
