import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "knowledge/research", element: L(() => import("./ResearchPage")) },
    { path: "knowledge/research/artifacts", element: L(() => import("./ArtifactsPage")) },
    { path: "knowledge/research/artifacts/:lineageId", element: L(() => import("./ArtifactsPage")) },
    { path: "knowledge/research/embodiment", element: L(() => import("./EmbodimentPage")) },
    { path: "knowledge/research/ip", element: L(() => import("./IpAnalysisPage")) },
  ],
  nav: {
    "/knowledge": [
      { path: "/knowledge/research", key: "nav.knowledge.research", zh: "外部检索与深度研究" },
      { path: "/knowledge/research/embodiment", key: "nav.knowledge.embodiment", zh: "实施例草稿" },
      { path: "/knowledge/research/ip", key: "nav.knowledge.ip", zh: "专利 / IP 分析" },
      { path: "/knowledge/research/artifacts", key: "nav.knowledge.artifacts", zh: "研究产物" },
    ],
  },
};

export default module;
