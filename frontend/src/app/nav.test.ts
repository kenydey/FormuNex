import { describe, expect, it } from "vitest";
import { NAV, type NavItem } from "./nav";
import { router } from "./router";

/**
 * 左侧菜单清单测试（菜单合并与精简方案 A + 第二轮精简）：新菜单结构即唯一事实源。
 * 逐项编码 46 个组内叶项 + 我的工作台的 path / 中文文案 / permission / when / order，
 * 防止后续改动悄悄增删入口、改权限或打乱顺序。路由本身在 router.tsx 中，菜单只呈现。
 * 第二轮：知识检索 6、文献与研究 6、研发需求 3、配方与实验 9、寻优与分析 6、
 * 文档与发布 4、物料与法规 3、系统管理 9（含桌面端限定 1 项，Web 可见 8 项）。
 */
type Leaf = { path: string; zh: string; permission?: string; hasWhen?: boolean };
type Group = { path: string; zh: string; leaves: Leaf[] };

const EXPECTED: Group[] = [
  { path: "/dashboard", zh: "我的工作台", leaves: [] }, // 独立项，无子项
  {
    path: "/knowledge",
    zh: "知识检索",
    leaves: [
      { path: "/knowledge/search", zh: "检索" },
      { path: "/knowledge/quality", zh: "检索质量运维" },
      { path: "/knowledge/sources", zh: "知识源与目录树" },
      { path: "/knowledge/chat", zh: "知识问答" },
      { path: "/knowledge/reviews", zh: "检索证据审计" },
      { path: "/knowledge/memory", zh: "Agent 记忆" },
    ],
  },
  {
    path: "/knowledge-library",
    zh: "文献与研究",
    leaves: [
      { path: "/knowledge/library", zh: "文献库" },
      { path: "/knowledge/research", zh: "外部检索与深度研究" },
      { path: "/knowledge/research/embodiment", zh: "实施例草稿" },
      { path: "/knowledge/research/ip", zh: "专利 / IP 分析" },
      { path: "/knowledge/research/artifacts", zh: "研究产物" },
      { path: "/knowledge/wiki", zh: "Wiki 知识库" },
    ],
  },
  {
    path: "/spec",
    zh: "研发需求",
    leaves: [
      { path: "/spec/projects", zh: "项目与规格契约" },
      { path: "/spec/paths", zh: "主路径向导" },
      { path: "/spec/sources", zh: "项目资料" },
    ],
  },
  {
    path: "/lab",
    zh: "配方与实验",
    leaves: [
      { path: "/lab/formulations", zh: "配方编辑" },
      { path: "/lab/doe", zh: "DOE 设计" },
      { path: "/lab/work-records", zh: "实验工作记录" },
      { path: "/lab/measurements", zh: "测量与复核" },
      { path: "/lab/methods", zh: "方法与仪器" },
      { path: "/lab/eln", zh: "外部 ELN" },
      { path: "/lab/recommend", zh: "AI 配方推荐" },
      { path: "/lab/inverse-design", zh: "逆向设计" },
      { path: "/lab/substitution", zh: "原料替代" },
    ],
  },
  {
    path: "/cockpit",
    zh: "寻优与分析",
    leaves: [
      { path: "/cockpit/optimization", zh: "寻优闭环" },
      { path: "/cockpit/uncertainty", zh: "不确定性诊断" },
      { path: "/cockpit/rheology", zh: "流变拟合" },
      { path: "/cockpit/properties", zh: "配方物性" },
      { path: "/cockpit/models", zh: "代理模型" },
      { path: "/cockpit/surface", zh: "响应曲面" },
    ],
  },
  {
    path: "/plm",
    zh: "文档与发布",
    leaves: [
      { path: "/plm/products", zh: "产品与修订" },
      { path: "/plm/formula-mappings", zh: "配方数据库映射" },
      { path: "/plm/label-designer", zh: "标签设计器" },
      { path: "/plm/regulatory", zh: "法规助手" },
    ],
  },
  {
    path: "/materials",
    zh: "物料与法规",
    leaves: [
      { path: "/materials/substances", zh: "物质与法规数据" },
      { path: "/materials/test-records", zh: "试验记录" },
      { path: "/materials/raw", zh: "原料" },
      { path: "/knowledge/graph", zh: "材料知识图谱" },
    ],
  },
  {
    path: "/admin",
    zh: "系统管理",
    leaves: [
      { path: "/admin/users", zh: "用户与角色", permission: "admin.users" },
      { path: "/admin/companies", zh: "公司配置", permission: "admin.companies" },
      { path: "/admin/audit", zh: "审计日志", permission: "audit.read" },
      { path: "/admin/connectors", zh: "连接器" },
      { path: "/admin/settings", zh: "设置中心", permission: "admin.llm" },
      { path: "/admin/mcp-approvals", zh: "MCP 工具审批" },
      { path: "/admin/org-dashboard", zh: "组织看板" },
      { path: "/knowledge/notebooklm", zh: "NotebookLM" },
      { path: "/admin/desktop", zh: "桌面端与本机服务", hasWhen: true },
    ],
  },
];

const flatLeaves = (items: NavItem[]): NavItem[] =>
  items.flatMap((i) => (i.children ? flatLeaves(i.children) : [i]));

const collectRoutePaths = (routes: readonly unknown[], base: string, out: Set<string>): void => {
  for (const r of routes as Array<{ path?: string; children?: unknown[] }>) {
    const full = r.path ? `${base}/${r.path}`.replace(/\/+/g, "/") : base;
    if (r.path) out.add(full);
    if (r.children) collectRoutePaths(r.children, full, out);
  }
};

describe("nav (menu checklist, scheme A)", () => {
  it("top-level order, groups and dashboard match the expected map", () => {
    expect(NAV.map((g) => g.path)).toEqual(EXPECTED.map((g) => g.path));
    NAV.forEach((g, i) => {
      expect(g.zh).toBe(EXPECTED[i].zh);
      expect(g.order).toBe(i);
    });
    expect(NAV[0].children).toBeUndefined(); // dashboard is a standalone leaf
  });

  it("every group has exactly the expected leaves in order, with unchanged permission/when", () => {
    for (const [gi, g] of NAV.entries()) {
      const expected = EXPECTED[gi].leaves;
      const actual = g.children ?? [];
      expect(actual.map((l) => l.path)).toEqual(expected.map((l) => l.path));
      actual.forEach((leaf, li) => {
        expect(leaf.zh).toBe(expected[li].zh);
        expect(leaf.permission).toBe(expected[li].permission);
        expect(leaf.order).toBe(li);
        if (expected[li].hasWhen) expect(typeof leaf.when).toBe("function");
        else expect(leaf.when).toBeUndefined();
      });
    }
  });

  it("47 group leaves + dashboard = 48 entries, no duplicate paths or keys", () => {
    const leaves = flatLeaves(NAV);
    expect(leaves).toHaveLength(48);
    expect(new Set(leaves.map((l) => l.path)).size).toBe(48);
    const keys = NAV.flatMap((g) => [g.key, ...(g.children ?? []).map((l) => l.key)]);
    expect(keys.every((k) => k.length > 0)).toBe(true);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("every menu leaf path exists in the router (no dead links)", () => {
    const routes = new Set<string>();
    collectRoutePaths(router.routes as unknown[], "", routes);
    for (const leaf of flatLeaves(NAV)) {
      const ok = [...routes].some(
        (r) => r === leaf.path || r === `${leaf.path}/*` || r.startsWith(`${leaf.path}/`),
      );
      expect(ok, `menu path ${leaf.path} has no route`).toBe(true);
    }
  });
});
