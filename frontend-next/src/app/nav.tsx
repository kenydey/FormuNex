import {
  AuditOutlined, BookOutlined, ControlOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined,
  FileProtectOutlined, SettingOutlined, SlidersOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { MODULES, type NavGroup } from "./modules";

export interface NavItem {
  path: string;
  key: string; // i18n key
  zh: string;
  icon?: ReactNode;
  children?: NavItem[];
  permission?: string;
  /** Shown only when this returns true (e.g. desktop-only pages). */
  when?: () => boolean;
}

/** 生命周期五步导航 + 物料与法规库 + 系统管理 (方案 §3.2). */
const BASE_NAV: NavItem[] = [
  { path: "/dashboard", key: "nav.dashboard", zh: "我的工作台", icon: <DashboardOutlined /> },
  { path: "/knowledge", key: "nav.knowledge", zh: "① 知识与情报中心", icon: <BookOutlined />, children: [
    { path: "/knowledge/search", key: "nav.knowledge.search", zh: "检索" },
    { path: "/knowledge/deep", key: "nav.knowledge.deep", zh: "深度检索（PageIndex）" },
    { path: "/knowledge/sources", key: "nav.knowledge.sources", zh: "知识源与目录树" },
  ] },
  { path: "/spec", key: "nav.spec", zh: "② 研发需求工作台", icon: <ControlOutlined />, children: [
    { path: "/spec/projects", key: "nav.spec.projects", zh: "项目与规格契约" },
  ] },
  { path: "/lab", key: "nav.lab", zh: "③ 配方与 DOE 实验室", icon: <ExperimentOutlined />, children: [
    { path: "/lab/formulations", key: "nav.lab.formulations", zh: "配方编辑" },
    { path: "/lab/doe", key: "nav.lab.doe", zh: "DOE 设计" },
    { path: "/lab/tasks", key: "nav.lab.tasks", zh: "实验任务" },
    { path: "/lab/review", key: "nav.lab.review", zh: "测量复核" },
    { path: "/lab/methods", key: "nav.lab.methods", zh: "方法与仪器" },
    { path: "/lab/eln", key: "nav.lab.eln", zh: "外部 ELN" },
  ] },
  { path: "/cockpit", key: "nav.cockpit", zh: "④ AI 寻优驾驶舱", icon: <SlidersOutlined />, children: [
    { path: "/cockpit/pareto", key: "nav.cockpit.pareto", zh: "帕累托与定型" },
    { path: "/cockpit/conformal", key: "nav.cockpit.conformal", zh: "预测区间与偏差" },
  ] },
  { path: "/plm", key: "nav.plm", zh: "⑤ PLM 文档与发布", icon: <FileProtectOutlined />, children: [
    { path: "/plm/products", key: "nav.plm.products", zh: "产品与修订" },
    { path: "/plm/label-designer", key: "nav.plm.labels", zh: "标签设计器" },
    { path: "/plm/regulatory", key: "nav.plm.regulatory", zh: "法规助手" },
  ] },
  { path: "/materials", key: "nav.materials", zh: "物料与法规库", icon: <DatabaseOutlined />, children: [
    { path: "/materials/substances", key: "nav.materials.substances", zh: "物质与法规数据" },
    { path: "/materials/raw", key: "nav.materials.raw", zh: "原料" },
  ] },
  { path: "/admin", key: "nav.admin", zh: "系统管理", icon: <SettingOutlined />, children: [
    { path: "/admin/users", key: "nav.admin.users", zh: "用户与角色", permission: "admin.users" },
    { path: "/admin/audit", key: "nav.admin.audit", zh: "审计日志", permission: "audit.read" },
    { path: "/admin/connectors", key: "nav.admin.connectors", zh: "连接器" },
  ] },
];

/** Base navigation plus the pages each feature module adds to a group (app/modules.ts). */
export const NAV: NavItem[] = BASE_NAV.map((g) => ({
  ...g,
  children: g.children && [...g.children, ...MODULES.flatMap((m) => m.nav[g.path as NavGroup] ?? [])],
}));

export const AUDIT_ICON = <AuditOutlined />;
