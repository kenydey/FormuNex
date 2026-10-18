import { isDesktop } from "./bridge";

/**
 * Desktop notification for something that finished while the window was in the background (long background
 * tasks). No-op in the browser or while the window is visible.
 */
export async function notifyDesktop(title: string, body: string): Promise<void> {
  if (!isDesktop() || !document.hidden) return;
  try {
    const n = await import("@tauri-apps/plugin-notification");
    let granted = await n.isPermissionGranted();
    if (!granted) granted = (await n.requestPermission()) === "granted";
    if (granted) n.sendNotification({ title, body });
  } catch {
    // notifications are best-effort
  }
}
