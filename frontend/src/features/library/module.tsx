import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// The legacy Knowledge Hub modal's tabs become these pages (wiki / graph / materials belong to other areas).
const module: FeatureModule = {
  routes: [
    { path: "knowledge/library", element: L(() => import("./LiteratureLibraryPage")) },
    { path: "knowledge/manifest", element: L(() => import("./ManifestPage")) },
    { path: "knowledge/collections", element: L(() => import("./CollectionsPage")) },
    { path: "knowledge/retrieval", element: L(() => import("./RetrievalProbePage")) },
    { path: "knowledge/quality", element: L(() => import("./QualityPage")) },
    { path: "knowledge/memory", element: L(() => import("./MemoryPage")) },
    { path: "knowledge/notebooklm", element: L(() => import("./NotebookLMPage")) },
    { path: "knowledge/parsing", element: L(() => import("./ParsingPage")) },
  ],
  nav: {
    "/knowledge": [
      { path: "/knowledge/retrieval", key: "nav.knowledge.retrieval", zh: "检索探针", order: 2 },
      { path: "/knowledge/quality", key: "nav.knowledge.quality", zh: "质量运营", order: 7 },
      { path: "/knowledge/memory", key: "nav.knowledge.memory", zh: "Agent 记忆", order: 8 },
      { path: "/knowledge/parsing", key: "nav.knowledge.parsing", zh: "解析与结构识别", order: 6 },
    ],
    "/knowledge-library": [
      { path: "/knowledge/library", key: "nav.knowledge.library", zh: "文献库", order: 0 },
      { path: "/knowledge/manifest", key: "nav.knowledge.manifest", zh: "文献冻结与筛选", order: 1 },
      { path: "/knowledge/collections", key: "nav.knowledge.collections", zh: "智能集合", order: 2 },
      { path: "/knowledge/notebooklm", key: "nav.knowledge.notebooklm", zh: "NotebookLM", order: 3 },
    ],
  },
};

export default module;
