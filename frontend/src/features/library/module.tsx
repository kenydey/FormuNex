import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { TabRedirect } from "@/shared/hubTabs";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
// The legacy Knowledge Hub modal's tabs become these pages (wiki / graph / materials belong to other areas).
// Menu round 2: library + manifest + collections merge into the library hub; retrieval + quality + parsing
// merge into the ops hub.
// Menu round 3: quality ops moves into the search hub as a tab (quality route redirects there);
// the Agent-memory menu entry is dropped in favour of a link in the chat page header (route unchanged).
const module: FeatureModule = {
  routes: [
    { path: "knowledge/library", element: L(() => import("./LibraryHubPage")) },
    { path: "knowledge/manifest", element: <TabRedirect to="/knowledge/library" tab="frozen" /> },
    { path: "knowledge/collections", element: <TabRedirect to="/knowledge/library" tab="smart" /> },
    { path: "knowledge/retrieval", element: <TabRedirect to="/knowledge/search" tab="ops" extra={{ opstab: "probe" }} /> },
    { path: "knowledge/quality", element: <TabRedirect to="/knowledge/search" tab="ops" /> },
    { path: "knowledge/memory", element: L(() => import("./MemoryPage")) },
    { path: "knowledge/parsing", element: <TabRedirect to="/knowledge/search" tab="ops" extra={{ opstab: "parsing" }} /> },
  ],
  nav: {
    "/data": [
      { path: "/knowledge/library", key: "nav.knowledge.library", zh: "文献库", order: 4, sub: "knowledge", desc: "文献编目、集合与记忆" },
    ],
  },
};

export default module;
