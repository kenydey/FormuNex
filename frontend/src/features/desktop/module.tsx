import type { FeatureModule } from "@/app/modules";
import { L } from "@/app/lazy";
import { isDesktop } from "@/desktop/bridge";

// The first-run wizard (/setup) lives outside the shell and is registered in app/router.tsx.
const module: FeatureModule = {
  routes: [{ path: "admin/desktop", element: L(() => import("./DesktopSettingsPage")) }],
  nav: { "/admin": [{ path: "/admin/desktop", key: "nav.admin.desktop", zh: "桌面端与本机服务", when: isDesktop, order: 7 }] },
};

export default module;
