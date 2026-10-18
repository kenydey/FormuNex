// @vitest-environment node
// 本文件必须跑在 node 环境而非 jsdom：jsdom 的 AbortSignal 会覆盖全局，
// 而 Request 仍是 Node 的 undici——undici 用全局 AbortSignal 创建 signal，
// 却用内部 AbortSignal 类校验，于是连自己创建的 signal 都拒绝
// （authFetch 里 new Request(target, request) 抛 "Expected signal to be an
// instance of AbortSignal"）。node 环境下两者同源，无此问题。
(globalThis as unknown as { window: unknown }).window = {
  location: { origin: "http://localhost:3000" },
};

import { afterEach, describe, expect, it, vi } from "vitest";
import { authFetch, getAccessToken, logoutBody, refreshAccessToken, rememberRefreshToken, setAccessToken, setRefreshStore, type RefreshStore } from "./auth";
import { setApiBase } from "./base";

function memoryStore(initial: string | null): RefreshStore & { value: string | null } {
  const s = { value: initial, get: async () => s.value, set: async (t: string | null) => { s.value = t; } };
  return s;
}

afterEach(() => {
  vi.restoreAllMocks();
  setAccessToken(null);
  setRefreshStore(null);
  setApiBase(null);
});

describe("desktop session (keychain refresh token, API on another origin)", () => {
  it("refreshes with the stored token in the body and keeps the rotated one", async () => {
    setApiBase("http://127.0.0.1:8000");
    const store = memoryStore("r1");
    setRefreshStore(store);
    const fetchMock = vi.fn(async (req: Request) => {
      expect(req.url).toBe("http://127.0.0.1:8000/api/identity/refresh");
      expect(await req.json()).toEqual({ refresh_token: "r1" });
      return new Response(JSON.stringify({ access_token: "a2", refresh_token: "r2" }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(await refreshAccessToken()).toBe("a2");
    expect(store.value).toBe("r2");
  });

  it("forgets a rejected refresh token and does not call the server without one", async () => {
    setRefreshStore(memoryStore(null));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await refreshAccessToken()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    const store = memoryStore("bad");
    setRefreshStore(store);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    expect(await refreshAccessToken()).toBeNull();
    expect(store.value).toBeNull();
  });

  it("sends app-relative and page-origin requests to the API base", async () => {
    setApiBase("https://fx.example.com");
    setAccessToken("tok");
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (req: Request) => {
      seen.push(`${req.method} ${req.url} ${req.headers.get("Authorization")}`);
      return new Response("{}", { status: 200 });
    }));
    await authFetch("/api/lab/tasks");
    await authFetch(new Request(`${window.location.origin}/api/x`, { method: "POST", body: "{}" }));
    expect(seen).toEqual(["GET https://fx.example.com/api/lab/tasks Bearer tok", "POST https://fx.example.com/api/x Bearer tok"]);
    expect(getAccessToken()).toBe("tok");
  });

  it("login keeps the token, logout sends and forgets it", async () => {
    const store = memoryStore(null);
    setRefreshStore(store);
    await rememberRefreshToken("r9");
    expect(store.value).toBe("r9");
    expect(await logoutBody()).toEqual({ refresh_token: "r9" });
    expect(store.value).toBeNull();
    setRefreshStore(null);
    expect(await logoutBody()).toBeUndefined();
  });
});
