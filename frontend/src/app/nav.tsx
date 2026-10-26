import {
  AuditOutlined, BookOutlined, ControlOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  FileProtectOutlined, FileTextOutlined, SettingOutlined, SlidersOutlined,
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
  /** Shown only when this returns true (e.g. desktop-only pages). */
  when?: () => boolean;
  /** Display order within the parent (groups: top-level order). NAV is sorted by this, never by append order. */
  order: number;
}

/** 菜单（左侧菜单合并与精简方案 A）：组顺序与组内顺序均由显式 order 决定。组父项 path 仅作分组键，无对应路由。 */
const BASE_NAV: NavItem[] = [
  { path: "/dashboard", key: "nav.dashboard", zh: "我的工作台", icon: <DashboardOutlined />, order: 0 },
  { path: "/knowledge", key: "nav.knowledge", zh: "知识检索", icon: <BookOutlined />, order: 1, children: [
    { path: "/knowledge/search", key: "nav.knowledge.search", zh: "检索", order: 0 },
    { path: "/knowledge/sources", key: "nav.knowledge.sources", zh: "知识源与目录树", order: 1 },
  ] },
  { path: "/knowledge-library", key: "nav.literature", zh: "文献与研究", icon: <FileTextOutlined />, order: 2, children: [] },
  { path: "/spec", key: "nav.spec", zh: "研发需求", icon: <ControlOutlined />, order: 3, children: [
    { path: "/spec/projects", key: "nav.spec.projects", zh: "项目与规格契约", order: 1 },
  ] },
  { path: "/lab", key: "nav.lab", zh: "配方与实验", icon: <ExperimentOutlined />, order: 4, children: [
    { path: "/lab/formulations", key: "nav.lab.formulations", zh: "配方编辑", order: 1 },
    { path: "/lab/doe", key: "nav.lab.doe", zh: "DOE 设计", order: 2 },
    { path: "/lab/measurements", key: "nav.lab.measurements", zh: "测量与复核", order: 4 },
    { path: "/lab/methods", key: "nav.lab.methods", zh: "方法与仪器", order: 7 },
    { path: "/lab/eln", key: "nav.lab.eln", zh: "外部 ELN", order: 8 },
  ] },
  { path: "/cockpit", key: "nav.cockpit", zh: "寻优与分析", icon: <SlidersOutlined />, order: 5, children: [
    { path: "/cockpit/optimization", key: "nav.cockpit.optimization", zh: "寻优闭环", order: 3 },
    { path: "/cockpit/uncertainty", key: "nav.cockpit.uncertainty", zh: "不确定性诊断", order: 2 },
    { path: "/cockpit/rheology", key: "nav.cockpit.rheology", zh: "流变拟合", order: 5 },
    { path: "/cockpit/properties", key: "nav.cockpit.properties", zh: "配方物性", order: 4 },
    { path: "/cockpit/models", key: "nav.cockpit.models", zh: "代理模型", order: 0 },
    { path: "/cockpit/surface", key: "nav.cockpit.surface", zh: "响应曲面", order: 1 },
  ] },
  { path: "/plm", key: "nav.plm", zh: "文档与发布", icon: <FileProtectOutlined />, order: 6, children: [
    { path: "/plm/products", key: "nav.plm.products", zh: "产品与修订", order: 0 },
    { path: "/plm/formula-mappings", key: "nav.plm.formula_mappings", zh: "配方数据库映射", order: 1 },
    { path: "/plm/label-designer", key: "nav.plm.labels", zh: "标签设计器", order: 2 },
    { path: "/plm/regulatory", key: "nav.plm.regulatory", zh: "法规助手", order: 3 },
  ] },
  { path: "/materials", key: "nav.materials", zh: "物料与法规", icon: <DatabaseOutlined />, order: 7, children: [
    { path: "/materials/substances", key: "nav.materials.substances", zh: "物质与法规数据", order: 0 },
    { path: "/materials/test-records", key: "nav.materials.test_records", zh: "物质毒理/生态记录", order: 2 },
    { path: "/materials/raw", key: "nav.materials.raw", zh: "原料", order: 1 },
  ] },
  { path: "/admin", key: "nav.admin", zh: "系统管理", icon: <SettingOutlined />, order: 8, children: [
    { path: "/admin/users", key: "nav.admin.users", zh: "用户与角色", permission: "admin.users", order: 1 },
    { path: "/admin/companies", key: "nav.admin.companies", zh: "公司配置", permission: "admin.companies", order: 2 },
    { path: "/admin/audit", key: "nav.admin.audit", zh: "审计日志", permission: "audit.read", order: 4 },
  ] },
];

/** Base navigation plus the pages each feature module adds to a group (app/modules.ts). Groups and children are sorted by explicit `order`. */
const byOrder = (a: NavItem, b: NavItem) => a.order - b.order;

export const NAV: NavItem[] = BASE_NAV.map((g) => ({
  ...g,
  children: g.children && [...g.children, ...MODULES.flatMap((m) => m.nav[g.path as NavGroup] ?? [])].sort(byOrder),
})).sort(byOrder);

export const AUDIT_ICON = <AuditOutlined />;
