import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { authFetch } from "./auth";

/** Typed client generated from the backend's OpenAPI (scripts/gen-api.mjs). */
export const api = createClient<paths>({ baseUrl: window.location.origin, fetch: authFetch });

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
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
    throw new ApiError(result.response.status, err?.detail ?? err ?? result.response.statusText);
  }
  return result.data as T;
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
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? data ?? res.statusText);
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Download a protected file (PDF / Word / ZIP) as a blob URL. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await authFetch(path);
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return URL.createObjectURL(await res.blob());
}

export async function download(path: string, filename: string): Promise<void> {
  const url = await fetchBlobUrl(path);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
