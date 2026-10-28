import type { RouteObject } from "react-router-dom";
import type { NavItem } from "./nav";
import chat from "@/features/chat/module";
import library from "@/features/library/module";
import research from "@/features/research/module";
import wiki from "@/features/wiki/module";
import formulation from "@/features/formulation/module";
import design from "@/features/design/module";
import workbench from "@/features/workbench/module";
import requirements from "@/features/requirements/module";
import settings from "@/features/settings/module";
import desktopModule from "@/features/desktop/module";

/** Navigation groups a feature module can add pages to (方案 §3.2). */
export type NavGroup = "/data" | "/spec" | "/lab" | "/cockpit" | "/product" | "/materials" | "/admin";

/** A feature area's routes (children of the authenticated shell) and its navigation entries. */
export interface FeatureModule {
  routes: RouteObject[];
  nav: Partial<Record<NavGroup, NavItem[]>>;
}

export const MODULES: FeatureModule[] = [chat, library, research, wiki, formulation, design, workbench, requirements, settings, desktopModule];
