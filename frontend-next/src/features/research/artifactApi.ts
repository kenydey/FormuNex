import { call } from "@/api/client";
import { authFetch } from "@/api/auth";
import { ApiError } from "@/api/client";
import { dispositionName } from "./lib";
import type { ArtifactLineage, ArtifactVersion, ExportFile } from "./types";

const enc = encodeURIComponent;

export const shelfPath = (projectId: string, name?: string) =>
  `/api/projects/${enc(projectId)}/exports${name ? `/${enc(name)}` : ""}`;

/** Save text to the project's export shelf. */
export const saveToShelf = (projectId: string, filename: string, content: string, contentType = "text/markdown") =>
  call<ExportFile>("POST", shelfPath(projectId), { filename, content, content_type: contentType });

/** New lineage with one staging version holding *content*; returns the lineage. */
export async function saveAsVersion(projectId: string, name: string, content: string, kind = "report"): Promise<ArtifactLineage> {
  const lineage = await call<ArtifactLineage>("POST", "/api/artifacts/lineages", { project_id: projectId, name, kind });
  await call<ArtifactVersion>("POST", `/api/artifacts/lineages/${enc(lineage.lineage_id)}/versions`, { content });
  return lineage;
}

/** Save a client-side text as a file. */
export function saveText(text: string, filename: string, type = "text/markdown;charset=utf-8") {
  saveBlob(new Blob([text], { type }), filename);
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** POST a JSON body and receive a file (``/api/reports/export``); errors keep the server's ``detail``. */
export async function postForFile(path: string, body: unknown, fallbackName: string): Promise<{ blob: Blob; filename: string }> {
  const res = await authFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      detail = j?.detail ?? j;
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return { blob: await res.blob(), filename: dispositionName(res.headers.get("Content-Disposition"), fallbackName) };
}
