import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";

// Routes and navigation entries of this feature area; merged by app/router.tsx and app/nav.tsx.
const module: FeatureModule = {
  routes: [
    { path: "lab/doe/classic", element: L(() => import("./ClassicDoePage")) },
    { path: "lab/doe/history", element: L(() => import("./DoeHistoryPage")) },
    { path: "lab/doe/levers", element: L(() => import("./LeversPage")) },
    { path: "lab/inverse-design", element: L(() => import("./InverseDesignPage")) },
    { path: "lab/substitution", element: L(() => import("./SubstitutionPage")) },
    { path: "lab/similar", element: L(() => import("./SimilarPage")) },
    { path: "lab/qc-report", element: L(() => import("./QcReportPage")) },
  ],
  nav: {
    "/lab": [
      { path: "/lab/doe/classic", key: "nav.lab.doe_classic", zh: "经典 DOE 与闭环" },
      { path: "/lab/doe/history", key: "nav.lab.doe_history", zh: "DOE 历史" },
      { path: "/lab/doe/levers", key: "nav.lab.levers", zh: "DOE 因子" },
      { path: "/lab/inverse-design", key: "nav.lab.inverse", zh: "逆向设计" },
      { path: "/lab/substitution", key: "nav.lab.substitution", zh: "原料替代" },
      { path: "/lab/similar", key: "nav.lab.similar", zh: "相似历史配方" },
      { path: "/lab/qc-report", key: "nav.lab.qc_report", zh: "检测报告解析" },
    ],
  },
};

export default module;
