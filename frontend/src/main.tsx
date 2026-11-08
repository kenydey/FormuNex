import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app/i18n";
import { initDesktop } from "./desktop/runtime";
import { isDesktop } from "./desktop/bridge";

// Desktop: read the shell settings (API server, keychain) before anything talks to the API.
async function boot() {
  try {
    await initDesktop();
  } catch (e) {
    console.error("desktop init failed", e);
    // P1-E12: a corrupt desktop.json used to fall through to a silent
    // web-style start (no settings, no wizard — a dead app that looked
    // alive). In the desktop shell, stop here with a recovery page.
    if (isDesktop()) {
      const { desktop } = await import("./desktop/bridge");
      const root = document.getElementById("root")!;
      root.innerHTML = "";
      const page = document.createElement("div");
      page.style.cssText = "max-width:560px;margin:120px auto;font-family:system-ui,sans-serif;padding:0 24px";
      page.innerHTML = `<h2>桌面配置损坏</h2><p>无法读取桌面配置（desktop.json 可能已损坏），应用无法启动。</p>` +
        `<p style="color:#666;word-break:break-all">${String(e)}</p>` +
        `<p>重置会删除桌面配置文件并重新进入首次设置向导；本机服务数据（Docker 卷）不受影响。</p>`;
      const reset = document.createElement("button");
      reset.textContent = "重置桌面配置并重进向导";
      reset.style.cssText = "padding:8px 16px;margin-right:12px;cursor:pointer";
      reset.onclick = async () => {
        try {
          await desktop.resetConfig();
        } catch {
          // already gone or unreadable — reloading still re-enters the wizard path
        }
        window.location.reload();
      };
      const retry = document.createElement("button");
      retry.textContent = "重试";
      retry.style.cssText = "padding:8px 16px;cursor:pointer";
      retry.onclick = () => window.location.reload();
      page.append(reset, retry);
      root.append(page);
      return;
    }
  }
  const { default: App } = await import("./app/App");
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void boot();
