import { defineConfig } from "@playwright/test";

// Browser smoke test against a running stack (see e2e/README.md); not part of `npm test`.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.FX_BASE_URL || "http://127.0.0.1:5174",
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
    screenshot: "only-on-failure",
  },
  reporter: [["list"]],
});
