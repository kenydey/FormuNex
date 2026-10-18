/**
 * Where the API lives. The web build is served by the API's own origin; the desktop shell serves the UI from
 * tauri://localhost and points it at the local stack (http://127.0.0.1:<port>) or a remote server.
 */
let base: string | null = null;

export function apiBase(): string {
  return base ?? window.location.origin;
}

export function setApiBase(url: string | null): void {
  base = url ? url.replace(/\/+$/, "") : null;
}

/** Resolve an app-relative API path (``/api/…``, ``/health``) against the API base. */
export function apiUrl(input: string | URL): URL {
  if (input instanceof URL) return rebase(input);
  if (/^[a-z][a-z0-9+.-]*:/i.test(input)) return rebase(new URL(input));
  return new URL(input.replace(/^\/+/, ""), `${apiBase()}/`);
}

/** A URL that was resolved against the page origin (e.g. by openapi-fetch) is moved onto the API base. */
export function rebase(url: URL): URL {
  if (base === null || url.origin !== window.location.origin) return url;
  return new URL(`${url.pathname.replace(/^\/+/, "")}${url.search}`, `${apiBase()}/`);
}
