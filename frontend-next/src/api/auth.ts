/**
 * Session handling (方案 §4.4): the access token lives in memory only; the refresh token is an httpOnly cookie
 * (path /api/identity) the browser sends to /api/identity/refresh. A 401 triggers one refresh and one retry;
 * concurrent 401s share a single refresh call.
 */
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

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(new Request(absolute("/api/identity/refresh"), { method: "POST", credentials: "same-origin" }));
        if (!res.ok) {
          setAccessToken(null);
          return null;
        }
        const body = (await res.json()) as { access_token: string };
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
  return new URL(input, window.location.origin);
}

/** fetch with the bearer token; on 401 refresh once and retry. */
export async function authFetch(input: Request | string | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(absolute(input), init);
  const attempt = (token: string | null) => {
    const r = request.clone();
    const headers = new Headers(r.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(new Request(r, { headers, credentials: "same-origin" }));
  };
  const res = await attempt(accessToken);
  const path = new URL(request.url, window.location.origin).pathname;
  if (res.status !== 401 || NO_RETRY.includes(path)) return res;
  const token = await refreshAccessToken();
  return token ? attempt(token) : res;
}
