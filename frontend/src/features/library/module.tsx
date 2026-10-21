import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// The legacy Knowledge Hub modal's tabs become these pages (wiki / graph / materials belong to other areas).
// Menu round 2: library + manifest + collections merge into the library hub; retrieval + quality + parsing
// merge into the ops hub; NotebookLM keeps its route but its nav entry moves to the admin group.
const module: FeatureModule = {
  routes: [
    { path: "knowledge/library", element: L(() => import("./LibraryHubPage")) },
    { path: "knowledge/manifest", element: <TabRedirect to="/knowledge/library" tab="frozen" /> },
    { path: "knowledge/collections", element: <TabRedirect to="/knowledge/library" tab="smart" /> },
    { path: "knowledge/retrieval", element: <TabRedirect to="/knowledge/quality" tab="probe" /> },
    { path: "knowledge/quality", element: L(() => import("./OpsHubPage")) },
    { path: "knowledge/memory", element: L(() => import("./MemoryPage")) },
    { path: "knowledge/notebooklm", element: L(() => import("./NotebookLMPage")) },
    { path: "knowledge/parsing", element: <TabRedirect to="/knowledge/quality" tab="parsing" /> },
  ],
  nav: {
    "/knowledge": [
      { path: "/knowledge/quality", key: "nav.knowledge.quality", zh: "检索质量运维", order: 1 },
      { path: "/knowledge/memory", key: "nav.knowledge.memory", zh: "Agent 记忆", order: 5 },
    ],
    "/knowledge-library": [
      { path: "/knowledge/library", key: "nav.knowledge.library", zh: "文献库", order: 0 },
    ],
    "/admin": [
      { path: "/knowledge/notebooklm", key: "nav.knowledge.notebooklm", zh: "NotebookLM", order: 7 },
    ],
  },
};

export default module;
