import { isDesktop } from "@/desktop/bridge";

/**
 * Save a file the user asked for. Browser: a download. Desktop: the native "Save as" dialog, then the bytes
 * are written to the chosen path (WKWebView cannot download blob: URLs). Resolves to false when cancelled.
 */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  if (isDesktop()) {
    const { save } = await import("@tauri-apps/plugin-dialog");
    const path = await save({ defaultPath: filename });
    if (!path) return false;
    const { writeFile } = await import("@tauri-apps/plugin-fs");
    await writeFile(path, new Uint8Array(await blob.arrayBuffer()));
    return true;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return true;
}

/** Save generated text (CSV / JSON / Markdown). */
export function saveText(text: string, filename: string, type = "text/plain;charset=utf-8"): Promise<boolean> {
  return saveBlob(new Blob([text], { type }), filename);
}
