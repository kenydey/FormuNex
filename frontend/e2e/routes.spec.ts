import { expect, test } from "@playwright/test";

// Visits every page of the new UI against a running stack (see e2e/README.md) and fails on uncaught errors,
// console errors, the error boundary, or 5xx API responses. Pages that need an id get a non-existent one and
// must show their own "not found" / empty state rather than crash.
const PASSWORD = "Fx-Smoke-2026!";

const ROUTES = [
  "/dashboard",
  "/knowledge/search", "/knowledge/deep", "/knowledge/sources", "/knowledge/chat", "/knowledge/reviews",
  "/knowledge/research", "/knowledge/research?tab=chat", "/knowledge/research?tab=deep", "/knowledge/research/artifacts",
  "/knowledge/research/artifacts/no-such-lineage", "/knowledge/research/embodiment", "/knowledge/research/ip",
  "/knowledge/wiki", "/knowledge/wiki-graph", "/knowledge/graph", "/knowledge/library", "/knowledge/manifest",
  "/knowledge/collections", "/knowledge/retrieval", "/knowledge/quality", "/knowledge/memory", "/knowledge/notebooklm",
  "/knowledge/parsing",
  "/spec/projects", "/spec/paths", "/spec/sources",
  "/lab/formulations", "/lab/doe", "/lab/doe/classic", "/lab/doe/history", "/lab/doe/levers", "/lab/tasks", "/lab/review",
  "/lab/methods", "/lab/eln", "/lab/experiments", "/lab/experiments/999999", "/lab/lineage", "/lab/lineage/no-such-lineage",
  "/lab/leaderboard", "/lab/recommend", "/lab/inverse-design", "/lab/substitution", "/lab/similar", "/lab/qc-report",
  "/cockpit/pareto", "/cockpit/conformal", "/cockpit/workbench", "/cockpit/loop", "/cockpit/models", "/cockpit/surface",
  "/plm/products", "/plm/label-designer",
  "/materials/substances", "/materials/raw", "/materials/raw?tab=pending", "/materials/raw?tab=structure",
  "/admin/users", "/admin/audit", "/admin/connectors", "/admin/settings/model", "/admin/settings/keys",
  "/admin/settings/capabilities", "/admin/settings/env", "/admin/settings/deps", "/admin/settings/kb",
  "/admin/mcp-approvals", "/admin/org-dashboard",
];

test("every page renders without errors", async ({ page }) => {
  test.setTimeout(15 * 60_000);
  let current = "";
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(`${current}: pageerror ${String(e).slice(0, 300)}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    // Expected: 4xx from probes of optional features / missing ids (reported by the page itself), antd dev warning.
    if (/favicon|status of 4\d\d|findDOMNode/.test(text)) return;
    problems.push(`${current}: console ${text.slice(0, 300)}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 500 && r.url().includes("/api/")) problems.push(`${current}: HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
  });

  await page.goto("/login");
  await page.getByPlaceholder("用户名").fill("demo");
  await page.getByPlaceholder("密码").fill(PASSWORD);
  await page.getByRole("button", { name: /登\s?录/ }).click();
  await expect(page).toHaveURL(/dashboard/);

  // Second pass with a project selected: most pages are project-scoped and render only an empty state without one.
  const projectId = await page.evaluate(async () => {
    const list = (await (await fetch("/api/projects")).json()) as { id: string; title: string }[];
    return list.find((p) => p.title === "环氧底漆开发")?.id ?? list[0]?.id ?? null;
  });
  const passes: (string | null)[] = [null, projectId];
  for (const pid of passes) {
    await page.evaluate((id) => {
      const raw = JSON.parse(localStorage.getItem("formunex-ui") || '{"state":{},"version":0}');
      raw.state.projectId = id;
      localStorage.setItem("formunex-ui", JSON.stringify(raw));
    }, pid);
  for (const route of ROUTES) {
    current = `${route}${pid ? " [project]" : ""}`;
    await page.goto(route);
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
    await page.waitForTimeout(400);
    if (await page.getByText(/界面发生错误|页面资源加载失败/).count()) problems.push(`${current}: error boundary shown`);
    if (await page.getByText("页面不存在").count()) problems.push(`${current}: not found page`);
  }
  }
  console.log(problems.length ? problems.join("\n") : "no problems");
  expect(problems).toEqual([]);
});
