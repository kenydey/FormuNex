import { describe, expect, it } from "vitest";
import { NAV, deriveNav, visibleNav, type NavItem } from "./nav";
import { router } from "./router";

/**
 * 前端界面重构（2026-10-28 方案 §2）：8 组按产品开发逻辑顺序排列，叶项路径全部沿用旧 URL。
 * 本文件双重断言：新结构即唯一事实源；旧清单（重构前 nav.test.ts 的 EXPECTED，原样保留为 OLD）
 * 与新结构逐路径对照——零删减、权限与 when 不变。路由本身在 router.tsx 中，菜单只呈现。
 * 新组：我的工作台 0、资料检索 11、研发需求 2、配方与实验 9、寻优与分析 6、
 * 产品管理 4、材料库 3、系统管理 7（含桌面端限定 1 项，Web 可见 6 项）。
 */
type Leaf = { path: string; zh: string; permission?: string; permissions?: string[]; hasWhen?: boolean };
type Group = { path: string; zh: string; leaves: Leaf[] };

const EXPECTED: Group[] = [
  { path: "/dashboard", zh: "我的工作台", leaves: [] }, // 独立项，无子项
  {
    path: "/data",
    zh: "资料检索",
    leaves: [
      { path: "/knowledge/search", zh: "检索" },
      { path: "/knowledge/sources", zh: "知识源与目录树" },
      { path: "/knowledge/chat", zh: "知识问答" },
      { path: "/knowledge/reviews", zh: "检索证据审计" },
      { path: "/knowledge/library", zh: "文献库" },
      { path: "/knowledge/research", zh: "外部检索与深度研究" },
      { path: "/knowledge/research/artifacts", zh: "研究产物" },
      { path: "/knowledge/research/embodiment", zh: "实施例草稿" },
      { path: "/knowledge/research/ip", zh: "专利 / IP 分析" },
      { path: "/knowledge/wiki", zh: "Wiki 知识库" },
      { path: "/knowledge/graph", zh: "材料知识图谱" },
    ],
  },
  {
    path: "/spec",
    zh: "研发需求",
    leaves: [
      { path: "/spec/paths", zh: "主路径向导" },
      { path: "/spec/projects", zh: "项目与规格契约" },
    ],
  },
  {
    path: "/lab",
    zh: "配方与实验",
    leaves: [
      { path: "/lab/recommend", zh: "AI 配方推荐" },
      { path: "/lab/formulations", zh: "配方编辑" },
      { path: "/lab/doe", zh: "DOE 设计" },
      { path: "/lab/work-records", zh: "实验工作记录" },
      { path: "/lab/measurements", zh: "测量与复核" },
      { path: "/lab/substitution", zh: "原料替代" },
      { path: "/lab/inverse-design", zh: "逆向设计" },
      { path: "/lab/methods", zh: "方法与仪器" },
      { path: "/lab/eln", zh: "外部 ELN" },
    ],
  },
  {
    path: "/cockpit",
    zh: "寻优与分析",
    leaves: [
      { path: "/cockpit/models", zh: "代理模型" },
      { path: "/cockpit/surface", zh: "响应曲面" },
      { path: "/cockpit/uncertainty", zh: "不确定性诊断" },
      { path: "/cockpit/optimization", zh: "寻优闭环" },
      { path: "/cockpit/properties", zh: "配方物性" },
      { path: "/cockpit/rheology", zh: "流变拟合" },
    ],
  },
  {
    path: "/product",
    zh: "产品管理",
    leaves: [
      { path: "/plm/products", zh: "产品与修订" },
      { path: "/plm/formula-mappings", zh: "配方数据库映射" },
      { path: "/plm/label-designer", zh: "标签设计器" },
      { path: "/plm/regulatory", zh: "法规助手" },
    ],
  },
  {
    path: "/materials",
    zh: "材料库",
    leaves: [
      { path: "/materials/substances", zh: "物质与法规数据" },
      { path: "/materials/raw", zh: "原料" },
      { path: "/materials/test-records", zh: "物质毒理/生态记录" },
    ],
  },
  {
    path: "/admin",
    zh: "系统管理",
    leaves: [
      { path: "/admin/mcp-approvals", zh: "MCP 工具审批" },
      { path: "/admin/users", zh: "用户与角色", permission: "admin.users" },
      { path: "/admin/companies", zh: "公司配置", permission: "admin.companies" },
      { path: "/admin/settings", zh: "设置中心", permissions: ["admin.llm", "admin.connectors"] },
      { path: "/admin/audit", zh: "审计日志", permission: "audit.read" },
      { path: "/admin/org-dashboard", zh: "组织看板" },
      { path: "/admin/desktop", zh: "桌面端与本机服务", hasWhen: true },
    ],
  },
];

/** 重构前清单（旧 9 组 42 叶 + 工作台）：只用于零删减对照，路径与权限/when 必须与新结构逐项一致。 */
const OLD: Group[] = [
  { path: "/dashboard", zh: "我的工作台", leaves: [] },
  {
    path: "/knowledge",
    zh: "知识检索",
    leaves: [
      { path: "/knowledge/search", zh: "检索" },
      { path: "/knowledge/sources", zh: "知识源与目录树" },
      { path: "/knowledge/chat", zh: "知识问答" },
      { path: "/knowledge/reviews", zh: "检索证据审计" },
    ],
  },
  {
    path: "/knowledge-library",
    zh: "文献与研究",
    leaves: [
      { path: "/knowledge/library", zh: "文献库" },
      { path: "/knowledge/research", zh: "外部检索与深度研究" },
      { path: "/knowledge/research/artifacts", zh: "研究产物" },
      { path: "/knowledge/research/embodiment", zh: "实施例草稿" },
      { path: "/knowledge/research/ip", zh: "专利 / IP 分析" },
      { path: "/knowledge/wiki", zh: "Wiki 知识库" },
    ],
  },
  {
    path: "/spec",
    zh: "研发需求",
    leaves: [
      { path: "/spec/paths", zh: "主路径向导" },
      { path: "/spec/projects", zh: "项目与规格契约" },
    ],
  },
  {
    path: "/lab",
    zh: "配方与实验",
    leaves: [
      { path: "/lab/recommend", zh: "AI 配方推荐" },
      { path: "/lab/formulations", zh: "配方编辑" },
      { path: "/lab/doe", zh: "DOE 设计" },
      { path: "/lab/work-records", zh: "实验工作记录" },
      { path: "/lab/measurements", zh: "测量与复核" },
      { path: "/lab/substitution", zh: "原料替代" },
      { path: "/lab/inverse-design", zh: "逆向设计" },
      { path: "/lab/methods", zh: "方法与仪器" },
      { path: "/lab/eln", zh: "外部 ELN" },
    ],
  },
  {
    path: "/cockpit",
    zh: "寻优与分析",
    leaves: [
      { path: "/cockpit/models", zh: "代理模型" },
      { path: "/cockpit/surface", zh: "响应曲面" },
      { path: "/cockpit/uncertainty", zh: "不确定性诊断" },
      { path: "/cockpit/optimization", zh: "寻优闭环" },
      { path: "/cockpit/properties", zh: "配方物性" },
      { path: "/cockpit/rheology", zh: "流变拟合" },
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
      { path: "/materials/raw", zh: "原料" },
      { path: "/materials/test-records", zh: "物质毒理/生态记录" },
      { path: "/knowledge/graph", zh: "材料知识图谱" },
    ],
  },
  {
    path: "/admin",
    zh: "系统管理",
    leaves: [
      { path: "/admin/mcp-approvals", zh: "MCP 工具审批" },
      { path: "/admin/users", zh: "用户与角色", permission: "admin.users" },
      { path: "/admin/companies", zh: "公司配置", permission: "admin.companies" },
      { path: "/admin/settings", zh: "设置中心", permission: "admin.llm" },
      { path: "/admin/audit", zh: "审计日志", permission: "audit.read" },
      { path: "/admin/org-dashboard", zh: "组织看板" },
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

describe("nav (8-group IA, scheme §2)", () => {
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
        expect(leaf.permissions).toEqual(expected[li].permissions);
        expect(leaf.order).toBe(li);
        if (expected[li].hasWhen) expect(typeof leaf.when).toBe("function");
        else expect(leaf.when).toBeUndefined();
      });
    }
  });

  it("42 group leaves + dashboard = 43 entries, no duplicate paths or keys", () => {
    const leaves = flatLeaves(NAV);
    expect(leaves).toHaveLength(43);
    expect(new Set(leaves.map((l) => l.path)).size).toBe(43);
    const keys = NAV.flatMap((g) => [g.key, ...(g.children ?? []).map((l) => l.key)]);
    expect(keys.every((k) => k.length > 0)).toBe(true);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("zero deletion: leaf paths and permission/when match the pre-restructure inventory exactly", () => {
    const oldLeaves = OLD.flatMap((g) => g.leaves);
    // Group leaves only (dashboard is a standalone leaf, asserted by the structure test above).
    const newByPath = new Map(NAV.flatMap((g) => g.children ?? []).map((l) => [l.path, l]));
    expect([...newByPath.keys()].sort()).toEqual(oldLeaves.map((l) => l.path).sort());
    for (const old of oldLeaves) {
      const item = newByPath.get(old.path)!;
      expect(item.zh).toBe(old.zh);
      if (old.path === "/admin/settings") {
        // W1-6/D2: settings gate widened from admin.llm to admin.llm OR admin.connectors (nothing dropped, one added).
        expect(item.permission).toBeUndefined();
        expect(item.permissions).toEqual(["admin.llm", "admin.connectors"]);
      } else {
        expect(item.permission).toBe(old.permission);
        expect(item.permissions).toBeUndefined();
      }
      expect(typeof item.when === "function").toBe(!!old.hasWhen);
    }
    // Every leaf and every group carries a card description (group home subtitle / rail tooltip).
    expect(NAV.every((g) => !!g.desc)).toBe(true);
    expect(flatLeaves(NAV).every((l) => !!l.desc)).toBe(true);
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

describe("deriveNav (route → group/leaf)", () => {
  it("exact leaves resolve to their new groups", () => {
    expect(deriveNav("/knowledge/search")).toEqual({ group: expect.objectContaining({ path: "/data" }), leaf: expect.objectContaining({ path: "/knowledge/search" }) });
    expect(deriveNav("/knowledge/graph")?.group.path).toBe("/data");
    expect(deriveNav("/plm/products")?.group.path).toBe("/product");
    expect(deriveNav("/materials/raw")?.group.path).toBe("/materials");
    expect(deriveNav("/dashboard")?.group.path).toBe("/dashboard");
    expect(deriveNav("/dashboard")?.leaf).toBeNull();
  });

  it("detail pages chain onto their parent leaf; unmapped details keep only the group", () => {
    expect(deriveNav("/plm/products/abc")?.leaf?.path).toBe("/plm/products");
    expect(deriveNav("/plm/documents/doc-1/proofread")?.leaf).toBeNull();
    expect(deriveNav("/plm/documents/doc-1/proofread")?.group.path).toBe("/product");
    expect(deriveNav("/knowledge/sources/src-9")?.leaf?.path).toBe("/knowledge/sources");
    expect(deriveNav("/admin/settings/connectors")?.leaf?.path).toBe("/admin/settings");
    expect(deriveNav("/lab/provenance/node/1")).toEqual({ group: expect.objectContaining({ path: "/lab" }), leaf: null });
  });

  it("paths outside the shell resolve to null", () => {
    expect(deriveNav("/")).toBeNull();
    expect(deriveNav("/login")).toBeNull();
    expect(deriveNav("/nope")).toBeNull();
  });
});

describe("visibleNav (permission/when filtering)", () => {
  it("drops permission-gated leaves the user lacks, keeps the rest, and never drops a whole group that still has visible leaves", () => {
    const vis = visibleNav(() => false);
    expect(vis.map((g) => g.path)).toEqual(NAV.map((g) => g.path));
    expect(vis.find((g) => g.path === "/admin")?.children?.map((l) => l.path)).toEqual([
      "/admin/mcp-approvals",
      "/admin/org-dashboard",
    ]);
  });

  it("OR gate: settings is visible with either admin.llm or admin.connectors alone", () => {
    const adminChildren = (can: (p: string) => boolean) =>
      visibleNav(can).find((g) => g.path === "/admin")?.children?.map((l) => l.path) ?? [];
    expect(adminChildren((p) => p === "admin.llm")).toContain("/admin/settings");
    expect(adminChildren((p) => p === "admin.connectors")).toContain("/admin/settings");
    expect(adminChildren(() => false)).not.toContain("/admin/settings");
  });
});
