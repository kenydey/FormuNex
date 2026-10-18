/**
 * Desktop start-up (方案 §3.5): read the shell's settings, point the API client at the chosen server, keep the
 * refresh token in the keychain and route formunex:// deep links.
 */
import { create } from "zustand";
import { setRefreshStore, type RefreshStore } from "@/api/auth";
import { setApiBase } from "@/api/base";
import { deepLinkRoute, desktop, isDesktop, type DesktopInfo } from "./bridge";

interface DesktopState {
  info: DesktopInfo | null;
  setInfo: (info: DesktopInfo | null) => void;
}

/** The shell's settings as last read; `null` in the browser build. */
export const useDesktop = create<DesktopState>((set) => ({ info: null, setInfo: (info) => set({ info }) }));

export function keychainRefreshStore(base: string): RefreshStore {
  const key = `refresh:${base}`;
  return {
    get: () => desktop.keychainGet(key).catch(() => null),
    set: async (token) => {
      try {
        if (token) await desktop.keychainSet(key, token);
        else await desktop.keychainDelete(key);
      } catch {
        // keychain unavailable: the session then lasts until the app closes
      }
    },
  };
}

export function applyDesktopInfo(info: DesktopInfo): void {
  setApiBase(info.api_base);
  setRefreshStore(info.api_base ? keychainRefreshStore(info.api_base) : null);
  useDesktop.getState().setInfo(info);
}

/** Load the shell settings before the first API call. Resolves to null in the browser. */
export async function initDesktop(): Promise<DesktopInfo | null> {
  if (!isDesktop()) return null;
  const info = await desktop.info();
  applyDesktopInfo(info);
  return info;
}

/** True when the desktop app still has to run its first-run wizard. */
export function needsSetup(info: DesktopInfo | null): boolean {
  return !!info && (!info.config.setup_complete || !info.api_base);
}

/** Follow formunex:// links: cold start (plugin), and links forwarded by a second instance (shell event). */
export async function startDeepLinks(navigate: (route: string) => void): Promise<void> {
  if (!isDesktop()) return;
  const open = (urls: string[]) => {
    for (const url of urls) {
      const route = deepLinkRoute(url);
      if (route) {
        navigate(route);
        return;
      }
    }
  };
  try {
    const dl = await import("@tauri-apps/plugin-deep-link");
    const initial = await dl.getCurrent();
    if (initial?.length) open(initial);
    await dl.onOpenUrl(open);
  } catch {
    // deep links unavailable (e.g. not registered on this OS)
  }
  const { listen } = await import("@tauri-apps/api/event");
  await listen<string>("deep-link://open", (e) => navigate(e.payload));
}
