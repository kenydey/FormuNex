import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    // ``knowledge/wiki/<page path>`` opens a page (e.g. /knowledge/wiki/materials/e51.md); [[wikilinks]] link here.
    { path: "knowledge/wiki/*", element: L(() => import("./WikiPage")) },
    { path: "knowledge/wiki-graph", element: L(() => import("./WikiGraphPage")) },
    { path: "knowledge/graph", element: L(() => import("./KgGraphPage")) },
  ],
  nav: {
    "/knowledge-library": [
      { path: "/knowledge/wiki", key: "nav.knowledge.wiki", zh: "Wiki 知识库", order: 8 },
      { path: "/knowledge/wiki-graph", key: "nav.knowledge.wiki_graph", zh: "Wiki 链接图", order: 9 },
    ],
    "/materials": [
      { path: "/knowledge/graph", key: "nav.knowledge.graph", zh: "材料知识图谱", order: 2 },
    ],
  },
};

export default module;
