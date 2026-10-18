import { afterEach, describe, expect, it } from "vitest";
import { apiBase, apiUrl, rebase, setApiBase } from "./base";

afterEach(() => setApiBase(null));

describe("api base", () => {
  it("defaults to the page origin (web build)", () => {
    expect(apiBase()).toBe(window.location.origin);
    expect(apiUrl("/api/x?y=1").href).toBe(`${window.location.origin}/api/x?y=1`);
    const u = new URL("/api/x", window.location.origin);
    expect(rebase(u)).toBe(u);
  });

  it("points app paths and page-origin URLs at the desktop server, keeping a path prefix", () => {
    setApiBase("https://fx.example.com/formunex/");
    expect(apiBase()).toBe("https://fx.example.com/formunex");
    expect(apiUrl("/api/identity/login").href).toBe("https://fx.example.com/formunex/api/identity/login");
    expect(apiUrl("/health").href).toBe("https://fx.example.com/formunex/health");
    expect(rebase(new URL(`${window.location.origin}/api/a?b=1`)).href).toBe("https://fx.example.com/formunex/api/a?b=1");
    // absolute URLs elsewhere are left alone
    expect(apiUrl("https://other.example/x").href).toBe("https://other.example/x");
  });
});
