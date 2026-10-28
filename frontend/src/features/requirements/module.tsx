import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "spec/paths", element: L(() => import("./PathsPage")) },
    { path: "spec/sources", element: L(() => import("./ProjectSourcesPage")) },
    { path: "spec/projects/:projectId/sources", element: L(() => import("./ProjectSourcesPage")) },
  ],
  nav: {
    "/spec": [
      { path: "/spec/paths", key: "nav.spec.paths", zh: "主路径向导", order: 0, desc: "一步步选定研发主路径" },
    ],
  },
};

export default module;
