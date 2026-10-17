import { Empty, Result } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { authFetch } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useUi } from "@/app/store";

/** Save a blob under *filename* (the browser download). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

async function failure(res: Response): Promise<ApiError> {
  let detail: unknown = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    detail = body?.detail ?? body;
  } catch {
    // keep statusText
  }
  return new ApiError(res.status, detail);
}

/** POST JSON and download the file the server answers with (filename from Content-Disposition). */
export async function postDownload(path: string, body: unknown, fallbackName: string): Promise<string> {
  const res = await authFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw await failure(res);
  const m = /filename="?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") || "");
  const name = m?.[1] ?? fallbackName;
  saveBlob(await res.blob(), name);
  return name;
}

/** GET a protected text file (BibTeX / RIS) and save it. */
export async function getTextDownload(path: string, filename: string, mime: string): Promise<void> {
  const res = await authFetch(path);
  if (!res.ok) throw await failure(res);
  saveBlob(new Blob([await res.text()], { type: mime }), filename);
}

/** Render *children* with the current project id, or ask the user to pick a project in the header. */
export function WithProject({ children }: { children: (projectId: string) => ReactNode }) {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  if (!projectId) {
    return <Result icon={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={false} />}
      subTitle={t("library.need_project", "请先在顶部选择一个项目")} />;
  }
  return <>{children(projectId)}</>;
}
