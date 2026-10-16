import { lazy } from "react";

/** Lazy route element: ``{ path: "x", element: L(() => import("@/features/x/XPage")) }``. */
export const L = (f: () => Promise<{ default: React.ComponentType }>) => {
  const C = lazy(f);
  return <C />;
};
