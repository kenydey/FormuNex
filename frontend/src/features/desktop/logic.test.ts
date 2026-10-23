import { describe, expect, it } from "vitest";
import { formatBytes, memoryWarning, parseSeedResult, stackReady } from "./logic";

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

  it("reads the regulatory importer's JSON result after its log lines", () => {
    const out = ["导入法规库…", "WARNING app.db: x", "{", '  "skipped": false,', '  "release_id": "r1",', '  "stats": {',
      '    "substances_total": 131552,', '    "transport_rows": 2861,', '    "iecsc_marked": 13717,', '    "oel_rows": 410', "  }", "}"];
    expect(parseSeedResult(out)).toEqual({ skipped: false, substances: 131552, transport: 2861, iecsc: 13717, oel: 410 });
    expect(parseSeedResult(["{", '  "skipped": true,', '  "stats": {}', "}"])).toEqual(
      { skipped: true, substances: null, transport: null, iecsc: null, oel: null });
    expect(parseSeedResult(["no json here"])).toBeNull();
    expect(parseSeedResult(["{", "truncated"])).toBeNull();
  });
});
