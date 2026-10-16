import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "Fx-Smoke-2026!";
const SHOTS = process.env.FX_SHOTS || "e2e/screenshots";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder("用户名").fill("demo");
  await page.getByPlaceholder("密码").fill(PASSWORD);
  await page.getByRole("button", { name: /登\s?录/ }).click();
  await expect(page).toHaveURL(/dashboard/);
}

test("main path renders without errors", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|status of 401|findDOMNode/.test(m.text())) errors.push(m.text()); });
  // Ignored: the session probe's expected 401 before signing in, and antd's own dev-only StrictMode
  // findDOMNode warning (rc-motion; absent from production builds).

  await login(page);
  await expect(page.getByText("你好，演示用户")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-dashboard.png`, fullPage: true });

  // ② project + spec contract
  await page.goto("/spec/projects");
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByRole("textbox", { name: /项目/ }).fill("环氧底漆开发");
  await page.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/spec\/projects\//);
  await page.getByRole("tab", { name: "合规红线" }).click();
  await page.screenshot({ path: `${SHOTS}/02-spec.png`, fullPage: true });
  await page.getByRole("button", { name: /保\s?存/ }).click();
  await expect(page.getByText("规格契约已保存")).toBeVisible();

  // ③ formulation editor: open the seeded version → live GB 30000 panel
  await page.goto("/lab/formulations");
  await page.getByRole("button", { name: "打开版本" }).click();
  await page.getByRole("button", { name: /^v1/ }).first().click();
  await expect(page.getByText("H226").first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SHOTS}/03-formulation.png`, fullPage: true });

  // ③ DOE: orthogonal design, persisted
  await page.goto("/lab/doe");
  await page.getByRole("button", { name: "生成设计" }).first().click();
  await expect(page.getByText("设计结果")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/04-doe.png`, fullPage: true });

  // ⑤ product → revision → SDS
  const token = await page.evaluate(async () => (await (await fetch("/api/identity/refresh", { method: "POST" })).json()).access_token);
  const auth = { Authorization: `Bearer ${token}` };
  const product = await (await request.post("/api/plm/products", { data: { code: `FX-${Date.now()}`, name_zh: "FX-100 环氧底漆" }, headers: auth })).json();
  const rev = await (await request.post(`/api/plm/products/${product.id}/revisions`, { data: { formulation_version_id: "fv-demo" }, headers: auth })).json();
  await page.goto(`/plm/revisions/${rev.id}`);
  await page.getByRole("tab", { name: "文档" }).click();
  await page.getByRole("button", { name: "生成文档" }).click();
  await page.getByRole("spinbutton", { name: "闭杯闪点 ℃" }).fill("30");
  await page.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/documents\//, { timeout: 30_000 });
  await expect(page.getByText("结构化内容")).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/05-sds.png`, fullPage: true });

  await page.goto(`/plm/revisions/${rev.id}`);
  await page.getByRole("tab", { name: /发布门禁/ }).click();
  await expect(page.getByText("G1").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/06-gate.png`, fullPage: true });

  await page.goto("/plm/label-designer");
  await expect(page.getByText("GB 15258 要素齐全")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/07-label-designer.png`, fullPage: true });

  for (const path of ["/knowledge/search", "/knowledge/deep", "/knowledge/sources", "/lab/tasks", "/lab/review", "/lab/methods",
    "/lab/eln", "/cockpit/pareto", "/cockpit/conformal", "/plm/products", "/materials/substances", "/materials/raw",
    "/admin/users", "/admin/audit", "/admin/connectors"]) {
    await page.goto(path);
    await expect(page.locator(".ant-pro-page-container").first()).toBeVisible();
  }
  await page.goto("/admin/audit");
  await expect(page.getByText(/哈希链完整/)).toBeVisible();

  // English UI
  await page.getByRole("button", { name: "EN" }).click();
  await expect(page.getByText("Hash chain intact", { exact: false })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/08-audit-en.png`, fullPage: true });

  expect(errors).toEqual([]);
});
