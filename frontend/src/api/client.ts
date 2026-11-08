import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { authFetch } from "./auth";
import { saveBlob } from "@/shared/saveFile";

/** Typed client generated from the backend's OpenAPI (scripts/gen-api.mjs). */
export const api = createClient<paths>({ baseUrl: window.location.origin, fetch: authFetch });

export class ApiError extends Error {
  /** Celery id the backend hands back on a 503 (``X-FormuNex-Task-Id`` header), if any. */
  constructor(public status: number, public detail: unknown, public taskId?: string | null) {
    super(typeof detail === "string" ? detail : detailText(detail) || `HTTP ${status}`);
  }
}

function detailText(detail: unknown): string {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d))).join("；");
  }
  if (typeof detail === "object" && "message" in (detail as object)) return String((detail as { message: unknown }).message);
  return JSON.stringify(detail);
}

/** Unwrap an openapi-fetch result: data, or throw ApiError with the server's ``detail``. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const err = result.error as { detail?: unknown } | undefined;
    throw new ApiError(result.response.status, err?.detail ?? err ?? result.response.statusText,
      result.response.headers.get("x-formunex-task-id"));
  }
  return result.data as T;
}

/** P3-E2: a non-JSON error body used to land verbatim in the message —
 * a full HTML proxy error page. Downgrade HTML bodies to the status and
 * truncate long text bodies. */
function errorDetail(res: Response, data: unknown): unknown {
  const detail = (data as { detail?: unknown })?.detail ?? data ?? res.statusText;
  if (typeof detail !== "string") return detail;
  const trimmed = detail.trim();
  if (trimmed.startsWith("<")) return `HTTP ${res.status}`;
  return trimmed.length > 300 ? `${trimmed.slice(0, 300)}…` : trimmed;
}

/** Plain JSON call for endpoints whose OpenAPI body is free-form; still goes through auth + refresh. */
export async function call<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await authFetch(path, {
    method,
    headers: body === undefined || body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? safeJson(text) : undefined;
  if (!res.ok) throw new ApiError(res.status, errorDetail(res, data),
    res.headers.get("x-formunex-task-id"));
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Stage 5 / SR-076: downloads used to throw the browser's default status
 * text on failure, losing the server's reason (e.g. a withdrawn package
 * no longer served). Parse the error body exactly like call() does. */
async function throwDownloadError(res: Response): Promise<never> {
  const text = await res.text();
  const data = text ? safeJson(text) : undefined;
  throw new ApiError(res.status, errorDetail(res, data),
    res.headers.get("x-formunex-task-id"));
}

/** Download a protected file (PDF / Word / ZIP) as a blob URL. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await authFetch(path);
  if (!res.ok) await throwDownloadError(res);
  return URL.createObjectURL(await res.blob());
}

export async function download(path: string, filename: string): Promise<void> {
  const res = await authFetch(path);
  if (!res.ok) await throwDownloadError(res);
  await saveBlob(await res.blob(), filename);
}
