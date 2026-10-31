import {
  BookOutlined, ControlOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  FileProtectOutlined, SettingOutlined, SlidersOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { MODULES, type NavGroup } from "./modules";

export interface NavItem {
  /** Group parents use `path` only as a grouping key — there is no matching index route in router.tsx. */
  path: string;
  key: string; // i18n key
  zh: string;
  icon?: ReactNode;
  children?: NavItem[];
  permission?: string;
  /** Any-of gate (W1-6/D2): visible when the user holds any listed permission. */
  permissions?: string[];
  /** Shown only when this returns true (e.g. desktop-only pages). */
  when?: () => boolean;
  /** Display order within the parent (groups: top-level order). NAV is sorted by this, never by append order. */
  order: number;
  /** One-line card subtitle on the group home (zh fallback; English lives in locales/en-US.ts under `${key}.desc`). */
  desc?: string;
  /** Subgroup id within the group home (资料检索 only): search | qa | research | knowledge. */
  sub?: string;
}

/**
 * 前端界面重构（2026-10-28 方案 §2）：8 组按产品开发逻辑顺序排列，组父项 path 仅作分组键，无对应路由。
 * 叶项路径全部沿用旧 URL（零删减，见 nav.test.ts 的旧清单对照）；叶项归属由 BASE_NAV + 各 feature module 的
 * nav 共同构成，模块只改分组键与组内 order，不改任何路由路径。
 */
const BASE_NAV: NavItem[] = [
  { path: "/dashboard", key: "nav.dashboard", zh: "我的工作台", icon: <DashboardOutlined />, order: 0,
    desc: "任务、待签批与新手路径" },
  { path: "/data", key: "nav.data", zh: "资料检索", icon: <BookOutlined />, order: 1,
    desc: "文献、专利、Wiki 与问答：先查资料再立项", children: [
    { path: "/knowledge/search", key: "nav.knowledge.search", zh: "检索", order: 0, sub: "search", desc: "跨知识源检索文献与资料" },
    { path: "/knowledge/sources", key: "nav.knowledge.sources", zh: "知识源与目录树", order: 1, sub: "search", desc: "管理知识源与目录结构" },
  ] },
  { path: "/spec", key: "nav.spec", zh: "研发需求", icon: <ControlOutlined />, order: 2,
    desc: "立项：主路径向导与规格契约", children: [
    { path: "/spec/projects", key: "nav.spec.projects", zh: "项目与规格契约", order: 1, desc: "项目列表与需求规格契约" },
  ] },
  { path: "/lab", key: "nav.lab", zh: "配方与实验", icon: <ExperimentOutlined />, order: 3,
    desc: "配方设计、实验台账与复核", children: [
    { path: "/lab/formulations", key: "nav.lab.formulations", zh: "配方编辑", order: 1, desc: "编辑配方组成与版本" },
    { path: "/lab/doe", key: "nav.lab.doe", zh: "DOE 设计", order: 2, desc: "生成实验方案并导出记录表" },
    { path: "/lab/measurements", key: "nav.lab.measurements", zh: "测量与复核", order: 4, desc: "测量值录入、复核与解析" },
    { path: "/lab/methods", key: "nav.lab.methods", zh: "方法与仪器", order: 7, desc: "检测方法与仪器管理" },
    { path: "/lab/eln", key: "nav.lab.eln", zh: "外部 ELN", order: 8, desc: "对接外部电子实验记录本" },
  ] },
  { path: "/cockpit", key: "nav.cockpit", zh: "寻优与分析", icon: <SlidersOutlined />, order: 4,
    desc: "寻优闭环、不确定性与物性分析", children: [
    { path: "/cockpit/optimization", key: "nav.cockpit.optimization", zh: "寻优闭环", order: 3, desc: "贝叶斯多目标闭环优化" },
    { path: "/cockpit/uncertainty", key: "nav.cockpit.uncertainty", zh: "不确定性诊断", order: 2, desc: "区间、偏差与采集趋势诊断" },
    { path: "/cockpit/rheology", key: "nav.cockpit.rheology", zh: "流变拟合", order: 5, desc: "流变曲线拟合" },
    { path: "/cockpit/properties", key: "nav.cockpit.properties", zh: "配方物性", order: 4, desc: "配方物性计算与趋势" },
    { path: "/cockpit/models", key: "nav.cockpit.models", zh: "代理模型", order: 0, desc: "训练与管理代理模型" },
    { path: "/cockpit/surface", key: "nav.cockpit.surface", zh: "响应曲面", order: 1, desc: "响应曲面拟合与可视化" },
  ] },
  { path: "/product", key: "nav.product", zh: "产品管理", icon: <FileProtectOutlined />, order: 5,
    desc: "产品定型：产品、文档与发布", children: [
    { path: "/plm/products", key: "nav.plm.products", zh: "产品与修订", order: 0, desc: "产品列表、配方表与版本" },
    { path: "/plm/formula-mappings", key: "nav.plm.formula_mappings", zh: "配方数据库映射", order: 1, desc: "配方与产品数据库的映射关系" },
    { path: "/plm/label-designer", key: "nav.plm.labels", zh: "标签设计器", order: 2, desc: "设计 GHS 安全标签" },
    { path: "/plm/regulatory", key: "nav.plm.regulatory", zh: "法规助手", order: 3, desc: "法规合规问答与检查" },
  ] },
  { path: "/materials", key: "nav.materials", zh: "材料库", icon: <DatabaseOutlined />, order: 6,
    desc: "原料与物质主数据", children: [
    { path: "/materials/substances", key: "nav.materials.substances", zh: "物质与法规数据", order: 0, desc: "13 万物质主数据与法规信息" },
    { path: "/materials/test-records", key: "nav.materials.test_records", zh: "物质毒理/生态记录", order: 2, desc: "毒理与生态记录，SDS §11/§12 数据来源" },
    { path: "/materials/raw", key: "nav.materials.raw", zh: "原料", order: 1, desc: "原料主数据与导入" },
  ] },
  { path: "/admin", key: "nav.admin", zh: "系统管理", icon: <SettingOutlined />, order: 7,
    desc: "用户、公司、审计与系统设置", children: [
    { path: "/admin/users", key: "nav.admin.users", zh: "用户与角色", permission: "admin.users", order: 1, desc: "用户账号与角色权限" },
    { path: "/admin/companies", key: "nav.admin.companies", zh: "公司配置", permission: "admin.companies", order: 2, desc: "公司信息、Logo 与模板" },
    { path: "/admin/audit", key: "nav.admin.audit", zh: "审计日志", permission: "audit.read", order: 4, desc: "审计链与操作日志" },
  ] },
];

/** Base navigation plus the pages each feature module adds to a group (app/modules.ts). Groups and children are sorted by explicit `order`. */
const byOrder = (a: NavItem, b: NavItem) => a.order - b.order;

export const NAV: NavItem[] = BASE_NAV.map((g) => ({
  ...g,
  children: g.children && [...g.children, ...MODULES.flatMap((m) => m.nav[g.path as NavGroup] ?? [])].sort(byOrder),
})).sort(byOrder);

/** Navigation after permission/when filtering (presentation only; no permission change). Groups left with zero visible children are hidden. */
export function visibleNav(can: (p: string) => boolean): NavItem[] {
  const out: NavItem[] = [];
  for (const g of NAV) {
    const groupVisible = (!g.permission || can(g.permission)) && (!g.permissions || g.permissions.some(can)) && (!g.when || g.when());
    if (!g.children) {
      if (groupVisible) out.push(g);
      continue;
    }
    const children = g.children.filter((l) => (!l.permission || can(l.permission)) && (!l.permissions || l.permissions.some(can)) && (!l.when || l.when()));
    if (children.length > 0) out.push({ ...g, children });
  }
  return out;
}

export interface DerivedNav {
  group: NavItem;
  /** The matched leaf, or null for detail pages with no exact leaf (group still derived from the first segment). */
  leaf: NavItem | null;
}

const FIRST_SEGMENT_GROUP: Record<string, string> = {
  dashboard: "/dashboard",
  knowledge: "/data",
  spec: "/spec",
  lab: "/lab",
  cockpit: "/cockpit",
  plm: "/product",
  materials: "/materials",
  admin: "/admin",
};

/**
 * Derive the active group/leaf from a pathname (方案 §4.2：左栏选中、切换条选中全部由当前路由派生）。
 * Leaf match is the longest leaf path that equals the pathname or is its prefix (detail pages chain onto
 * their parent leaf, e.g. /plm/products/:id → 产品与修订）; otherwise the group falls back to the first
 * URL segment. Returns null only for paths outside the shell (e.g. /login, /setup, /).
 */
export function deriveNav(pathname: string): DerivedNav | null {
  let best: { group: NavItem; leaf: NavItem } | null = null;
  for (const g of NAV) {
    for (const l of g.children ?? []) {
      if (pathname === l.path || pathname.startsWith(`${l.path}/`)) {
        if (!best || l.path.length > best.leaf.path.length) best = { group: g, leaf: l };
      }
    }
  }
  if (best) return best;
  const group = NAV.find((g) => g.path === FIRST_SEGMENT_GROUP[pathname.split("/")[1] ?? ""]);
  return group ? { group, leaf: null } : null;
}
