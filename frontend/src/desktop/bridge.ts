/**
 * Bridge to the Tauri shell (frontend-next/src-tauri, 方案 §3.5). Everything here is a no-op / unavailable in
 * the browser build; Tauri modules are imported lazily so the web bundle does not need them at start-up.
 */
export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export type Mode = "local" | "remote";

export interface DesktopConfig {
  mode: Mode | null;
  remote_url: string;
  api_port: number;
  image: string;
  version: string;
  setup_complete: boolean;
  docker_path: string | null;
}

export interface DesktopInfo {
  config: DesktopConfig;
  api_base: string | null;
  data_dir: string;
  config_dir: string;
  app_version: string;
  platform: string;
}

export interface DockerStatus {
  installed: boolean;
  path: string | null;
  client_version: string | null;
  server_version: string | null;
  compose_version: string | null;
  mem_total_bytes: number | null;
  cpus: number | null;
  message: string | null;
}

export interface ServiceState { service: string; state: string; health: string; status: string }
export interface Health { reachable: boolean; status: string; detail: Record<string, unknown> | null; error: string | null }
export interface StackStatus { services: ServiceState[]; health: Health }
export interface BackupInfo { path: string; name: string; bytes: number }

/** Shell commands (src-tauri/src/commands.rs). Errors arrive as the shell's (Chinese) message string. */
export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const core = await import("@tauri-apps/api/core");
  try {
    return await core.invoke<T>(cmd, args);
  } catch (e) {
    throw new Error(typeof e === "string" ? e : e instanceof Error ? e.message : JSON.stringify(e));
  }
}

export const desktop = {
  info: () => invoke<DesktopInfo>("desktop_info"),
  saveConfig: (config: DesktopConfig) => invoke<DesktopConfig>("save_config", { config }),
  dockerStatus: () => invoke<DockerStatus>("docker_status"),
  stackStatus: () => invoke<StackStatus>("stack_status"),
  probeHealth: (base: string) => invoke<Health>("probe_health", { base }),
  stackUp: () => invoke<void>("stack_up"),
  stackDown: () => invoke<void>("stack_down"),
  stackRestart: () => invoke<void>("stack_restart"),
  stackPull: () => invoke<void>("stack_pull"),
  importImages: (path: string) => invoke<void>("import_images", { path }),
  stackLogs: (service?: string, tail?: number) => invoke<string>("stack_logs", { service: service ?? null, tail: tail ?? null }),
  backupNow: () => invoke<BackupInfo>("backup_now"),
  listBackups: () => invoke<BackupInfo[]>("list_backups"),
  upgrade: (version: string) => invoke<DesktopConfig>("stack_upgrade", { version }),
  createAdmin: (username: string, displayName: string) => invoke<string>("create_admin", { username, displayName }),
  keychainGet: (key: string) => invoke<string | null>("keychain_get", { key }),
  keychainSet: (key: string, value: string) => invoke<void>("keychain_set", { key, value }),
  keychainDelete: (key: string) => invoke<void>("keychain_delete", { key }),
};

/** Subscribe to `docker compose` output lines streamed by long stack operations. */
export async function onStackLog(handler: (line: string) => void): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("stack://log", (e) => handler(e.payload));
}

/** `formunex://plm/packages/42?x=1` → `/plm/packages/42?x=1` (mirrors `deep_link_route` in lib.rs). */
export function deepLinkRoute(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "formunex:") return null;
  // WHATWG parsing puts the first segment of a non-special scheme into ``host`` (or into the path).
  const raw = `/${parsed.host}${parsed.pathname}`.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";
  if (raw.includes("..")) return null;
  return parsed.search ? `${raw}${parsed.search}` : raw;
}
