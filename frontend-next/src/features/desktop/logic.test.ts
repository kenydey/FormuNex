import { describe, expect, it } from "vitest";
import { formatBytes, memoryWarning, stackReady } from "./logic";

const svc = (service: string, state = "running") => ({ service, state, health: "", status: "" });

describe("desktop logic", () => {
  it("formats sizes and warns below the memory recommendation", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(3 * 1024 ** 2)).toBe("3.0 MB");
    expect(memoryWarning(8 * 1024 ** 3)).toBe("8.0");
    expect(memoryWarning(15.6 * 1024 ** 3)).toBeNull();
    expect(memoryWarning(null)).toBeNull();
  });

  it("is ready only when the API answers and the required services run", () => {
    const up = { reachable: true, status: "ok", detail: null, error: null };
    const all = ["postgres", "redis", "kg", "api", "worker"].map((s) => svc(s));
    expect(stackReady({ health: up, services: all })).toBe(true);
    expect(stackReady({ health: up, services: all.filter((s) => s.service !== "worker") })).toBe(false);
    expect(stackReady({ health: { ...up, reachable: false }, services: all })).toBe(false);
    expect(stackReady({ health: up, services: [...all.slice(0, 4), svc("worker", "restarting")] })).toBe(false);
    expect(stackReady(null)).toBe(false);
  });
});
