import { expect, test, type Browser, type Page } from "@playwright/test";

const PASSWORD = "Fx-Smoke-2026!";
const SHOTS = process.env.FX_SHOTS || "e2e/screenshots";

// Separation of duties (enforced by the backend): the document approver must
// differ from the creator, one person cannot sign two release steps, and a
// measurement reviewer must differ from its enterer. All users share one
// password; see e2e/seed.py.
const USERS = {
  demo: "demo",
  checker: "checker",
  signer_lead: "signer_lead",
  signer_ehs: "signer_ehs",
  signer_qa: "signer_qa",
  signer_rm: "signer_rm",
} as const;

async function login(page: Page, user: string = USERS.demo) {
  await page.goto("/login");
  await page.getByPlaceholder("用户名").fill(user);
  await page.getByPlaceholder("密码").fill(PASSWORD);
  await page.getByRole("button", { name: /登\s?录/ }).click();
  await expect(page).toHaveURL(/dashboard/);
}

/** A fresh logged-in browser context for a second user (approver / signer / reviewer). */
async function loginAs(browser: Browser, user: string): Promise<{ ctx: import("@playwright/test").BrowserContext; page: Page }> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await login(page, user);
  return { ctx, page };
}

/** Fill the electronic-signature modal (password + optional comment) and confirm. */
async function signWithPassword(page: Page, comment?: string) {
  const modal = page.getByRole("dialog");
  await expect(modal.getByLabel(/登录密码/)).toBeVisible();
  await modal.getByLabel(/登录密码/).fill(PASSWORD);
  if (comment !== undefined) await modal.getByLabel(/意见/).fill(comment);
  await modal.getByRole("button", { name: /确\s?定|OK/ }).click();
}

/** Create a product + revision through the UI only (no direct API calls). */
async function createProductRevision(page: Page): Promise<{ productId: string; revId: string }> {
  const code = `FX-${Date.now()}`;
  await page.goto("/plm/products");
  await page.getByRole("button", { name: "新建产品" }).click();
  await page.getByRole("dialog").getByLabel(/产品代码/).fill(code);
  await page.getByRole("dialog").getByLabel(/名称/).first().fill("FX-100 环氧底漆");
  await page.getByRole("dialog").getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/products\//);
  const productId = page.url().split("/plm/products/")[1];

  await page.getByRole("button", { name: "新建修订版" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox").first().click();
  await page.getByRole("option", { name: /FX-100 底漆 v1/ }).click();
  await dialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/revisions\//);
  return { productId, revId: page.url().split("/plm/revisions/")[1] };
}

/** Generate one document via the GenerateDocModal on the revision page. Lands on the document page. */
async function generateDoc(page: Page, docType: "sds" | "tds" | "label" | "coa", opts: { lang?: string; batchNo?: string } = {}) {
  await page.getByRole("tab", { name: "文档" }).click();
  await page.getByRole("button", { name: "生成文档" }).click();
  const dialog = page.getByRole("dialog");
  // doc_type select is the first combobox in the modal
  await dialog.getByRole("combobox").first().click();
  await page.getByRole("option", { name: docType === "label" ? "标签" : docType.toUpperCase(), exact: true }).click();
  if (opts.lang) {
    await dialog.getByRole("combobox").nth(1).click();
    await page.getByRole("option", { name: opts.lang === "en-US" ? "English" : "中文", exact: true }).click();
  }
  if (docType === "sds") {
    await dialog.getByLabel(/闭杯闪点/).fill("30");
  }
  if (docType === "tds") {
    await dialog.getByLabel(/产品说明/).fill("双组分环氧底漆，适用于钢结构防腐");
  }
  if (docType === "coa") {
    await dialog.getByLabel(/批号/).click();
    await page.getByRole("option", { name: opts.batchNo! }).click();
  }
  await dialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/documents\//, { timeout: 30_000 });
  await expect(page.getByText("结构化内容")).toBeVisible();
}

/** Approve the open document (as a different user than its creator). */
async function approveDocument(page: Page) {
  await page.getByRole("button", { name: /审核通过|签发 COA/ }).click();
  await signWithPassword(page);
  await expect(page.getByText(/已审核/)).toBeVisible();
}

test("main path renders without errors", async ({ page }) => {
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

  // ⑤ product → revision → SDS, all through the UI (no direct API calls)
  const { revId } = await createProductRevision(page);
  await generateDoc(page, "sds");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/05-sds.png`, fullPage: true });

  await page.goto(`/plm/revisions/${revId}`);
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

test("PLM publish lifecycle: TDS + COA + approve + sign-off + release", async ({ page, browser }) => {
  // Product + revision (UI), then the full required-doc set for the release gate:
  // SDS zh-CN + en-US, TDS zh-CN + en-US, label zh-CN, COA (one batch).
  const { productId, revId } = await createProductRevision(page);
  const docUrls: string[] = [];
  await generateDoc(page, "sds");
  docUrls.push(page.url());
  await page.goto(`/plm/revisions/${revId}`);
  await generateDoc(page, "sds", { lang: "en-US" });
  docUrls.push(page.url());
  await page.goto(`/plm/revisions/${revId}`);
  await generateDoc(page, "tds");
  docUrls.push(page.url());
  await page.goto(`/plm/revisions/${revId}`);
  await generateDoc(page, "tds", { lang: "en-US" });
  docUrls.push(page.url());
  await page.goto(`/plm/revisions/${revId}`);
  await generateDoc(page, "label");
  docUrls.push(page.url());

  // COA needs a batch with QC results: create both on the product page.
  const batchNo = `B-${Date.now().toString(36)}`;
  await page.goto(`/plm/products/${productId}`);
  await page.getByRole("button", { name: "新建批次" }).click();
  await page.getByRole("dialog").getByLabel(/批号/).fill(batchNo);
  await page.getByRole("dialog").getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page.getByText(batchNo)).toBeVisible();
  await page.getByRole("link", { name: "录入质检结果" }).first().click();
  const qcDialog = page.getByRole("dialog");
  await qcDialog.getByRole("button", { name: "添加一行" }).click();
  const qcInputs = qcDialog.locator("input");
  await qcInputs.nth(0).fill("density_g_cm3");
  await qcInputs.nth(1).fill("1.35");
  await qcDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page.getByText("已保存")).toBeVisible();

  await page.goto(`/plm/revisions/${revId}`);
  await generateDoc(page, "coa", { batchNo });
  docUrls.push(page.url());

  // Approve every document as the checker (creator cannot approve own docs).
  const { ctx: checkerCtx, page: checkerPage } = await loginAs(browser, USERS.checker);
  try {
    for (const url of docUrls) {
      await checkerPage.goto(url);
      await approveDocument(checkerPage);
    }
  } finally {
    await checkerCtx.close();
  }

  // Mark candidate → gate → waive any waivable open items → submit.
  await page.goto(`/plm/revisions/${revId}`);
  await page.getByRole("button", { name: "钦定为候选" }).click();
  await expect(page.getByRole("button", { name: "提交签批" })).toBeVisible();
  await page.getByRole("tab", { name: /发布门禁/ }).click();
  // Waive what the UI offers (only waivable checks get a button); the rest must pass.
  for (let i = 0; i < 5; i++) {
    const waive = page.getByRole("button", { name: "豁免" }).first();
    if (!(await waive.count())) break;
    await waive.click();
    await signWithPassword(page, "E2E 豁免：非阻断数据缺口");
    await expect(page.getByText("已豁免")).toBeVisible();
  }
  await page.getByRole("button", { name: "重新检查" }).click();
  await expect(page.getByRole("button", { name: "提交签批" })).toBeEnabled({ timeout: 30_000 });
  await page.getByRole("button", { name: "提交签批" }).click();
  await expect(page.getByText("完成")).toBeVisible();
  // Submit stays on the revision page; the package is listed under the 发布包 tab.
  await page.getByRole("tab", { name: "发布包" }).click();
  await page.locator("a[href*='/plm/packages/']").first().click();
  await expect(page).toHaveURL(/plm\/packages\//);
  const packageUrl = page.url();

  // Four-step sign-off, one distinct user per step.
  const signers = [USERS.signer_lead, USERS.signer_ehs, USERS.signer_qa, USERS.signer_rm];
  for (let i = 0; i < signers.length; i++) {
    const { ctx, page: signerPage } = await loginAs(browser, signers[i]);
    try {
      await signerPage.goto(packageUrl);
      const btn = i === signers.length - 1
        ? signerPage.getByRole("button", { name: "最终发布" })
        : signerPage.getByRole("button", { name: "签批通过" });
      await btn.click();
      await signWithPassword(signerPage);
      if (i < signers.length - 1) await expect(signerPage.getByText(/等待/)).toBeVisible();
    } finally {
      await ctx.close();
    }
  }
  await page.goto(packageUrl);
  await expect(page.getByText("重新推送连接器")).toBeVisible();
});

test("lab measurement → review → pareto winning", async ({ page, browser }) => {
  // DOE → dispatch as lab tasks.
  await login(page);
  await page.goto("/lab/doe");
  await page.getByRole("button", { name: "生成设计" }).first().click();
  await expect(page.getByText("设计结果")).toBeVisible();
  await page.getByRole("button", { name: "下发为实验任务" }).click();
  const dispatchDialog = page.getByRole("dialog");
  await dispatchDialog.getByRole("combobox").click();
  await dispatchDialog.getByRole("combobox").fill("density_g_cm3");
  await page.keyboard.press("Enter");
  await dispatchDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/lab\/tasks/);

  // Claim the first task, enter a measurement, submit for review.
  await page.getByRole("link", { name: "领取" }).first().click();
  const taskLink = page.locator("a[href*='/lab/tasks/']").first();
  await taskLink.click();
  await expect(page.getByText("按待测指标生成录入行")).toBeVisible();
  await page.getByRole("button", { name: "按待测指标生成录入行" }).click();
  const entryInputs = page.locator("input[type='number']");
  await entryInputs.first().fill("1.35");
  await page.getByRole("button", { name: "保存录入" }).click();
  await expect(page.getByText("测量记录")).toBeVisible();
  await page.getByRole("button", { name: "提交复核" }).click();

  // Review as the checker (enterer cannot review own data).
  const { ctx: checkerCtx, page: checkerPage } = await loginAs(browser, USERS.checker);
  try {
    await checkerPage.goto("/lab/review");
    await checkerPage.locator("tbody input[type='checkbox']").first().check();
    await checkerPage.getByRole("button", { name: "通过所选" }).click();
  } finally {
    await checkerCtx.close();
  }

  // Pareto: pick the measured metric as objective, compute, set winning.
  await page.goto("/cockpit/pareto");
  const objSelect = page.getByLabel(/目标/).locator("..").getByRole("combobox");
  await objSelect.click();
  await objSelect.fill("density_g_cm3");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "计算前沿" }).click();
  await expect(page.getByRole("button", { name: "设为定型" }).first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "设为定型" }).first().click();
  const winDialog = page.getByRole("dialog");
  await winDialog.getByRole("combobox").first().click();
  await page.getByRole("option").first().click();
  await winDialog.getByRole("combobox").nth(1).click();
  await page.getByRole("option", { name: /FX-100 底漆 v1/ }).click();
  await winDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page.getByText(/已生成修订版/)).toBeVisible();
});
