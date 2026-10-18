import { describe, expect, it } from "vitest";
import { deepLinkRoute, isDesktop } from "./bridge";

describe("desktop bridge", () => {
  it("is not desktop in the browser / jsdom", () => {
    expect(isDesktop()).toBe(false);
  });

  it("maps formunex:// links to app routes (same rules as the shell)", () => {
    expect(deepLinkRoute("formunex://plm/packages/42")).toBe("/plm/packages/42");
    expect(deepLinkRoute("formunex://lab/tasks?status=open")).toBe("/lab/tasks?status=open");
    expect(deepLinkRoute("formunex://dashboard/")).toBe("/dashboard");
    expect(deepLinkRoute("https://evil.example/x")).toBeNull();
    expect(deepLinkRoute("not a url")).toBeNull();
  });
});
