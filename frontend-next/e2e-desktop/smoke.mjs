// Desktop smoke test (plan §3.5 / §7): drives the real Tauri app through WebKitWebDriver + tauri-driver.
// Remote mode against a running FormuNex API: first-run wizard → connect → sign in → dashboard → a page
// that downloads through the native save dialog is reachable. See e2e-desktop/README.md.
import { spawn } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { remote } from "webdriverio";

const app = process.env.FX_APP || path.resolve("src-tauri/target/release/formunex");
const api = process.env.FX_API || "http://127.0.0.1:8000";
const user = process.env.FX_USER || "demo";
const password = process.env.FX_PASSWORD || "Fx-Smoke-2026!";
const shots = process.env.FX_SHOTS || "e2e-desktop/screenshots";
mkdirSync(shots, { recursive: true });

// Fresh desktop settings (first-run wizard).
rmSync(path.join(os.homedir(), ".config", "com.formunex.desktop"), { recursive: true, force: true });

const driver = spawn(process.env.TAURI_DRIVER || path.join(os.homedir(), ".cargo/bin/tauri-driver"), [], { stdio: ["ignore", "inherit", "inherit"] });
await new Promise((r) => setTimeout(r, 1500));

let browser;
const fail = (msg) => { throw new Error(msg); };
try {
  browser = await remote({
    hostname: "127.0.0.1", port: 4444, logLevel: "error",
    capabilities: { "tauri:options": { application: app }, "wdio:enforceWebDriverClassic": true },
  });
  const text = async () => (await browser.$("body")).getText();
  const waitText = (needle, timeout = 30000) =>
    browser.waitUntil(async () => (await text()).includes(needle), { timeout, timeoutMsg: `"${needle}" not shown` });

  await waitText("首次设置");
  await browser.saveScreenshot(`${shots}/01-setup.png`);
  await (await browser.$("//*[contains(text(),'连接服务器')]")).click();
  const url = await browser.$("input[placeholder='https://formunex.example.com']");
  await url.waitForDisplayed();
  await url.setValue(api);
  await (await browser.$("//button[contains(.,'测试并连接')]")).click();

  await (await browser.$("input[placeholder='用户名']")).waitForDisplayed({ timeout: 30000 });
  if (!(await text()).includes(api)) fail("login page does not show the server");
  await browser.saveScreenshot(`${shots}/02-login.png`);
  await (await browser.$("input[placeholder='用户名']")).setValue(user);
  await (await browser.$("input[placeholder='密码']")).setValue(password);
  await (await browser.$("//button[contains(.,'登') and contains(.,'录')]")).click();
  await waitText("待我签批");
  await browser.saveScreenshot(`${shots}/03-dashboard.png`);

  // client-side navigation inside the desktop app, API calls cross-origin to the server
  await browser.execute(() => window.history.pushState({}, "", "/plm/products"));
  await browser.execute(() => window.dispatchEvent(new PopStateEvent("popstate")));
  await waitText("产品");
  await browser.saveScreenshot(`${shots}/04-products.png`);
  const logs = await browser.execute(() => document.title);
  console.log("desktop smoke ok:", logs);
} catch (e) {
  if (browser) {
    await browser.saveScreenshot(`${shots}/failure.png`).catch(() => undefined);
    console.error("page text at failure:\n", await browser.$("body").getText().catch(() => ""));
  }
  process.exitCode = 1;
  console.error(e);
} finally {
  if (browser) await browser.deleteSession().catch(() => undefined);
  driver.kill();
}
