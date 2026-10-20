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
  if (comment !== undefined) await modal.locator("textarea").first().fill(comment);
  // SignatureModal's confirm button reads 签名确认, not the generic 确定.
  await modal.getByRole("button", { name: /签名确认|确\s?定|OK/ }).click();
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
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: /^FX-100 底漆 v1$/ }).click();
  await dialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/revisions\//);
  return { productId, revId: page.url().split("/plm/revisions/")[1] };
}

/** Generate one document via the GenerateDocModal on the revision page. Lands on the document page. */
async function generateDoc(page: Page, docType: "sds" | "tds" | "label" | "coa", opts: { lang?: string; batchNo?: string } = {}) {
  await page.getByRole("tab", { name: "文档" }).click();
  await page.getByRole("button", { name: "生成文档" }).click();
  const dialog = page.getByRole("dialog");
  // doc_type select is the first select in the modal. Click the Select
  // container, not the inner combobox input: with a preselected value the
  // .ant-select-selection-item span overlays the input and intercepts the
  // click, so getByRole("combobox").click() never lands.
  await dialog.locator(".ant-select").first().click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: new RegExp(`^${docType === "label" ? "标签" : docType.toUpperCase()}$`) }).click();
  if (opts.lang) {
    await dialog.locator(".ant-select").nth(1).click();
    await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: opts.lang === "en-US" ? /^English$/ : /^中文$/ }).click();
  }
  if (docType === "sds" || docType === "label") {
    // The classification-input section renders for label as well as SDS, and
    // both must classify from the SAME properties: a label generated without
    // the flash point classifies differently, which fails gate G5 (mixed
    // classification inputs), G8 (SDS/label mismatch) and G10 (the label's
    // classification carries a blocking "no flash point" data gap).
    await dialog.getByLabel(/闭杯闪点/).fill("30");
  }
  if (docType === "tds") {
    await dialog.getByLabel(/产品说明/).fill("双组分环氧底漆，适用于钢结构防腐");
    // A TDS with missing items cannot be approved: the builder requires at
    // least one typical-property row and one application-guide field, so
    // enter both here (same blur-commit typing as the QC/spec tables).
    await dialog.getByRole("button", { name: "添加一行" }).click();
    const propInputs = dialog.locator("tbody .ant-table-row").last().locator("input");
    await expect(propInputs.first()).toBeVisible();
    // Typical-property columns: key / 项目 / 数值 / 单位 / 方法
    await propInputs.nth(0).click();
    await propInputs.nth(0).pressSequentially("density_g_cm3");
    await page.keyboard.press("Tab");
    await propInputs.nth(1).click();
    await propInputs.nth(1).pressSequentially("密度");
    await page.keyboard.press("Tab");
    await propInputs.nth(2).click();
    await propInputs.nth(2).pressSequentially("1.35");
    await page.keyboard.press("Tab");
    await propInputs.nth(3).click();
    await propInputs.nth(3).pressSequentially("g/cm³");
    await page.keyboard.press("Tab");
    await dialog.getByLabel(/施工方式/).fill("无气喷涂、刷涂");
  }
  if (docType === "coa") {
    // Click the batch Select container (scoped to its form item), not the
    // inner combobox — same overlay lesson as the doc_type select above.
    await dialog.locator(".ant-form-item", { hasText: "批号" }).locator(".ant-select").click();
    await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: new RegExp(`^${opts.batchNo!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }).click();
  }
  await dialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/plm\/documents\//, { timeout: 30_000 });
  await expect(page.getByText("结构化内容")).toBeVisible();
}

/** Approve the open document (as a different user than its creator). */
async function approveDocument(page: Page) {
  await page.getByRole("button", { name: /审核通过|签发 COA/ }).click();
  await signWithPassword(page);
  // Assert on the status tag: the success toast carries the same text and a
  // bare getByText(/已审核/) strict-matches both while the toast is up.
  await expect(page.locator(".ant-tag", { hasText: "已审核" })).toBeVisible();
}

test("main path renders without errors", async ({ page }) => {
  // Same budget pattern as routes.spec.ts (15 min for ~100 pages): this flow
  // walks the core pages and cold Vite chunk compiles alone can eat most of
  // the 60s config default (measured 51–60s warm).
  test.setTimeout(3 * 60_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|status of 401|findDOMNode/.test(m.text())) errors.push(m.text()); });
  // Ignored: the session probe's expected 401 before signing in, and antd's own dev-only StrictMode
  // findDOMNode warning (rc-motion; absent from production builds).

  await login(page);
  await expect(page.getByText("你好，演示用户")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-dashboard.png`, fullPage: true });

  // 研发需求：project + spec contract
  await page.goto("/spec/projects");
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByRole("textbox", { name: /项目/ }).fill("环氧底漆开发");
  await page.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/spec\/projects\//);
  await page.getByRole("tab", { name: "合规红线" }).click();
  await page.screenshot({ path: `${SHOTS}/02-spec.png`, fullPage: true });
  await page.getByRole("button", { name: /保\s?存/ }).click();
  await expect(page.getByText("规格契约已保存")).toBeVisible();

  // 配方与实验：formulation editor: open the seeded version → live GB 30000 panel
  await page.goto("/lab/formulations");
  await page.getByRole("button", { name: "打开版本" }).click();
  await page.getByRole("button", { name: /^v1/ }).first().click();
  await expect(page.getByText("H226").first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SHOTS}/03-formulation.png`, fullPage: true });

  // 配方与实验：DOE: orthogonal design, persisted
  await page.goto("/lab/doe");
  await page.getByRole("button", { name: "生成设计" }).first().click();
  await expect(page.getByText("设计结果")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/04-doe.png`, fullPage: true });

  // 文档与发布：product → revision → SDS, all through the UI (no direct API calls)
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
  // The chain badge renders only once the audit query resolves; on a cold
  // page load (Vite chunk compile + query) that can exceed the default 5s
  // expect window even though the API itself answers in ~10ms. Wait for the
  // audit response — the signal the badge actually depends on — first.
  const auditLoaded = page.waitForResponse((r) => r.url().includes("/api/plm/audit") && r.status() === 200);
  await page.goto("/admin/audit");
  await auditLoaded;
  await expect(page.getByText(/哈希链完整/)).toBeVisible();

  // English UI
  await page.getByRole("button", { name: "EN" }).click();
  await expect(page.getByText("Hash chain intact", { exact: false })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/08-audit-en.png`, fullPage: true });

  expect(errors).toEqual([]);
});

test("PLM publish lifecycle: TDS + COA + approve + sign-off + release", async ({ page, browser }) => {
  // Budget like routes.spec.ts: six document generations, multi-user
  // approvals and a four-signer loop cannot fit the 60s config default —
  // every step completes, the test was killed mid-navigation by the total
  // budget. No step-level timeout is touched.
  test.setTimeout(5 * 60_000);
  await login(page);
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
  // The QC action is an <a> without href (ProductDetailPage batch table), so
  // it has no link role — scope a text locator to the batch's own table row.
  await page.locator(".ant-table-tbody .ant-table-row", { hasText: batchNo }).getByText("录入质检结果", { exact: true }).click();
  const qcDialog = page.getByRole("dialog");
  await qcDialog.getByRole("button", { name: "添加一行" }).click();
  const qcInputs = qcDialog.locator("input");
  // EditableProTable keeps cell values in its own form store and commits a
  // cell on blur; submitting while the last cell is still uncommitted races
  // that commit and intermittently sends results: [] (backend 422). Type
  // like a user, commit each cell by moving focus on, and blur the last
  // cell into the unit cell before confirming.
  await qcInputs.nth(0).click();
  await qcInputs.nth(0).pressSequentially("density_g_cm3");
  await page.keyboard.press("Tab");
  await qcInputs.nth(1).click();
  await qcInputs.nth(1).pressSequentially("1.35");
  await page.keyboard.press("Enter");
  await qcInputs.nth(2).click();
  await qcDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page.getByText("已保存")).toBeVisible();

  await page.goto(`/plm/revisions/${revId}`);
  // COA generation requires release specs (backend 422 with none). This
  // product has no linked project to copy a spec snapshot from, so enter one
  // release spec by hand on the 规格快照 tab. Same EditableRows editor as the
  // QC dialog: values commit cell-by-cell on blur, so type and move on with
  // Tab, then click the section title to blur the last cell before saving.
  await page.getByRole("tab", { name: "规格快照" }).click();
  const specPanel = page.locator(".ant-tabs-tabpane-active");
  await specPanel.getByRole("button", { name: "添加一行" }).first().click();
  const specRow = specPanel.locator(".ant-table-wrapper").first().locator("tbody .ant-table-row").last();
  const specInputs = specRow.locator("input");
  await expect(specInputs.first()).toBeVisible();
  // Release-spec columns: 指标 / 检验项目 / 单位 / 检验方法 / 下限 / 上限 / 判定(select, defaults 全数值比较)
  await specInputs.nth(0).click();
  await specInputs.nth(0).pressSequentially("density_g_cm3");
  await page.keyboard.press("Tab");
  await specInputs.nth(1).click();
  await specInputs.nth(1).pressSequentially("密度");
  await page.keyboard.press("Tab");
  await specInputs.nth(4).click();
  await specInputs.nth(4).pressSequentially("1.2");
  await page.keyboard.press("Tab");
  await specInputs.nth(5).click();
  await specInputs.nth(5).pressSequentially("1.5");
  await page.keyboard.press("Tab");
  await specPanel.getByText("出厂指标（COA 判定）").click();
  await specPanel.getByRole("button", { name: /保\s?存/ }).click();
  await expect(page.getByText("完成")).toBeVisible();

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
  // Exact name: the collapse headers of waivable checks contain 可豁免 in
  // their accessible name, and a substring match clicks the header instead.
  for (let i = 0; i < 5; i++) {
    const waive = page.getByRole("button", { name: "豁免", exact: true }).first();
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
  await page.keyboard.press("Escape"); // tags 模式下拉选后不自动关闭，先关掉再点确定，避免遮挡
  await dispatchDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page).toHaveURL(/lab\/tasks/);

  // Claim the first task, enter a measurement, submit for review.
  // The claim action is an <a> without href (TasksPage table actions), so it
  // has no link role in the a11y tree — scope a text locator to the first row.
  await page.locator(".ant-table-tbody .ant-table-row").first().getByText("领取", { exact: true }).click();
  const taskLink = page.locator("a[href*='/lab/tasks/']").first();
  await taskLink.click();
  await expect(page.getByText("按待测指标生成录入行")).toBeVisible();
  await page.getByRole("button", { name: "按待测指标生成录入行" }).click();
  // Entry rows render in a RevoGrid (TaskDetailPage), not form inputs: the
  // value cell is edited in place — double-click opens the cell editor, fill,
  // Enter commits (onAfteredit syncs the source that 保存录入 submits).
  const entryRow = page.getByRole("treegrid").getByRole("row", { name: /density_g_cm3/ });
  await expect(entryRow).toBeVisible();
  const valueCell = entryRow.getByRole("gridcell").nth(1);
  await valueCell.dblclick();
  const cellEditor = page.locator("revo-grid input").first();
  await expect(cellEditor).toBeVisible();
  await cellEditor.fill("1.35");
  await page.keyboard.press("Enter");
  await expect(valueCell).toHaveText(/1\.35/);
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
  // "目标" is a plain <span>, not a <label> (ParetoPage), so getByLabel
  // resolves nothing — anchor on the label's Space item and take the Select
  // rendered in the following item, then pick the visible dropdown row.
  const objSelect = page.locator(".ant-space-item", { has: page.getByText("目标", { exact: true }) })
    .locator("xpath=following-sibling::*[1]").locator(".ant-select");
  await objSelect.click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: /^density_g_cm3/ }).first().click();
  await page.keyboard.press("Escape"); // 多选下拉选后保持打开，关掉防遮挡
  await page.getByRole("button", { name: "计算前沿" }).click();
  await expect(page.getByRole("button", { name: "设为定型" }).first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "设为定型" }).first().click();
  const winDialog = page.getByRole("dialog");
  await winDialog.getByRole("combobox").first().click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option").first().click();
  await winDialog.getByRole("combobox").nth(1).click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: /^FX-100 底漆 v1$/ }).click();
  await winDialog.getByRole("button", { name: /确\s?定|OK/ }).click();
  await expect(page.getByText(/已生成修订版/)).toBeVisible();
});
