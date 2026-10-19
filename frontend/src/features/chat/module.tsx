import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "knowledge/chat", element: L(() => import("./ChatPage")) },
    { path: "knowledge/chat/:sessionId", element: L(() => import("./ChatPage")) },
    { path: "knowledge/reviews", element: L(() => import("./Reviewer")) },
    { path: "knowledge/reviews/:runId", element: L(() => import("./Reviewer")) },
  ],
  nav: {
    "/knowledge": [
      { path: "/knowledge/chat", key: "nav.knowledge.chat", zh: "知识问答", order: 4 },
      { path: "/knowledge/reviews", key: "nav.knowledge.reviews", zh: "检索证据审计", order: 5 },
    ],
  },
};

export default module;
