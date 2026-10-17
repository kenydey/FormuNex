/**
 * Session handling (方案 §4.4): the access token lives in memory only; the refresh token is an httpOnly cookie
 * (path /api/identity) the browser sends to /api/identity/refresh. A 401 triggers one refresh and one retry;
 * concurrent 401s share a single refresh call.
 *
 * Desktop (Tauri): the UI and the API are different origins, so no cookie — the refresh token comes back in the
 * response body and lives in the OS keychain behind a {@link RefreshStore}; it is sent in the request body.
 */
import { apiUrl, rebase } from "./base";
export interface SessionUser {
  id: string;
  username: string;
  display_name: string;
  roles: string[];
}

type Listener = (token: string | null) => void;

let accessToken: string | null = null;
let refreshing: Promise<string | null> | null = null;
const listeners = new Set<Listener>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  listeners.forEach((l) => l(token));
}

export function onTokenChange(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Where a non-cookie client keeps its refresh token (the desktop keychain). */
export interface RefreshStore {
  get(): Promise<string | null>;
  set(token: string | null): Promise<void>;
}

let refreshStore: RefreshStore | null = null;

export function setRefreshStore(store: RefreshStore | null): void {
  refreshStore = store;
}

/** Keep the refresh token a login / refresh returned (desktop only; browsers rely on the cookie). */
export async function rememberRefreshToken(token: string | null | undefined): Promise<void> {
  if (refreshStore && token) await refreshStore.set(token);
}

/** Body for /api/identity/logout: the stored refresh token (desktop), which is then forgotten. */
export async function logoutBody(): Promise<Record<string, string> | undefined> {
  if (!refreshStore) return undefined;
  const token = await refreshStore.get();
  await refreshStore.set(null);
  return token ? { refresh_token: token } : undefined;
}

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        let init: RequestInit = { method: "POST", credentials: "same-origin" };
        if (refreshStore) {
          const stored = await refreshStore.get();
          if (!stored) {
            setAccessToken(null);
            return null;
          }
          init = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: stored }) };
        }
        const res = await fetch(new Request(absolute("/api/identity/refresh"), init));
        if (!res.ok) {
          if (refreshStore && res.status === 401) await refreshStore.set(null);
          setAccessToken(null);
          return null;
        }
        const body = (await res.json()) as { access_token: string; refresh_token?: string };
        await rememberRefreshToken(body.refresh_token);
        setAccessToken(body.access_token);
        return body.access_token;
      } catch {
        setAccessToken(null);
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

const NO_RETRY = ["/api/identity/login", "/api/identity/refresh", "/api/identity/logout"];

function absolute(input: string | URL): URL {
  return apiUrl(input);
}

/** fetch with the bearer token; on 401 refresh once and retry. */
export async function authFetch(input: Request | string | URL, init?: RequestInit): Promise<Response> {
  let request = input instanceof Request ? input : new Request(absolute(input), init);
  const target = rebase(new URL(request.url));
  if (target.href !== request.url) request = new Request(target, request);
  const attempt = (token: string | null) => {
    const r = request.clone();
    const headers = new Headers(r.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(new Request(r, { headers, credentials: "same-origin" }));
  };
  const res = await attempt(accessToken);
  const path = new URL(request.url).pathname.replace(/^.*(?=\/api\/identity\/)/, "");
  if (res.status !== 401 || NO_RETRY.includes(path)) return res;
  const token = await refreshAccessToken();
  return token ? attempt(token) : res;
}
