import { afterEach, describe, expect, it, vi } from "vitest";
import { authFetch, getAccessToken, setAccessToken } from "./auth";

afterEach(() => { vi.restoreAllMocks(); setAccessToken(null); });

describe("authFetch", () => {
  it("sends the bearer token, refreshes once on 401 and retries", async () => {
    setAccessToken("old");
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (req: Request) => {
      const url = new URL(req.url).pathname;
      if (url === "/api/identity/refresh") return new Response(JSON.stringify({ access_token: "new" }), { status: 200 });
      seen.push(req.headers.get("Authorization") ?? "");
      return new Response("{}", { status: seen.length === 1 ? 401 : 200 });
    }));
    const res = await authFetch("/api/lab/tasks");
    expect(res.status).toBe(200);
    expect(seen).toEqual(["Bearer old", "Bearer new"]);
    expect(getAccessToken()).toBe("new");
  });

  it("gives up when the refresh fails and never retries the login call", async () => {
    setAccessToken("old");
    const fetchMock = vi.fn(async (req: Request) => new Response("{}", { status: new URL(req.url).pathname === "/api/identity/refresh" ? 401 : 401 }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await authFetch("/api/x")).status).toBe(401);
    expect(getAccessToken()).toBeNull();
    fetchMock.mockClear();
    await authFetch("/api/identity/login", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
