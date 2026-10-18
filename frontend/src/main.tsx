import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app/i18n";
import { initDesktop } from "./desktop/runtime";

// Desktop: read the shell settings (API server, keychain) before anything talks to the API.
async function boot() {
  try {
    await initDesktop();
  } catch (e) {
    console.error("desktop init failed", e);
  }
  const { default: App } = await import("./app/App");
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void boot();
