import { describe, expect, it } from "vitest";
import {
  disabledSkillIds, envFlagUpdates, groupBy, groupDependencies, isLargeDownload, manualMcpConfig, modelOptions, neo4jSummary,
  normalizeSettingsTab, recommendedModel, showsBaseUrl,
} from "./logic";
import type { DependencyInfo, EnvFlag, UnifiedSkill } from "./types";

describe("normalizeSettingsTab", () => {
  it("keeps current tabs, maps legacy ids and defaults to model", () => {
    expect(normalizeSettingsTab("env")).toBe("env");
    expect(normalizeSettingsTab("llm")).toBe("model");
    expect(normalizeSettingsTab("api")).toBe("keys");
    expect(normalizeSettingsTab("skills")).toBe("capabilities");
    expect(normalizeSettingsTab("advanced")).toBe("env");
    expect(normalizeSettingsTab("nonsense")).toBe("model");
    expect(normalizeSettingsTab(undefined)).toBe("model");
  });
});

describe("helpers", () => {
  it("groups in first-seen order", () => {
    expect(groupBy([{ g: "b" }, { g: "a" }, { g: "b" }], (r) => r.g).map(([k, v]) => [k, v.length])).toEqual([["b", 2], ["a", 1]]);
  });

  it("sends only changed env flags", () => {
    const f = (attr: string, value: boolean) => ({ attr, value, default: false }) as EnvFlag;
    expect(envFlagUpdates([f("a", true), f("b", false), f("c", false)], { a: true, b: true })).toEqual({ b: true });
  });

  it("orders dependency extras by the catalog order, unknown last", () => {
    const d = (pip_name: string, extra: string) => ({ pip_name, extra }) as DependencyInfo;
    expect(groupDependencies([d("x", "zzz"), d("y", "bo"), d("z", "llm"), d("w", "bo")]).map((g) => [g.extra, g.items.length]))
      .toEqual([["llm", 1], ["bo", 2], ["zzz", 1]]);
  });

  it("computes disabled skill ids without always-on skills", () => {
    const s = (id: string, enabled: boolean, activation_policy = "manual") => ({ id, enabled, activation_policy }) as UnifiedSkill;
    const skills = [s("a", true), s("b", false), s("c", true, "always-on"), s("d", true)];
    expect(disabledSkillIds(skills, "a", false)).toEqual(["a", "b"]);
    expect(disabledSkillIds(skills, "b", true)).toEqual([]);
  });

  it("builds a manual MCP config", () => {
    expect(manualMcpConfig(" ", "npx ", " -y  @x/server ")).toEqual({ mcpServers: { mcp: { command: "npx", args: ["-y", "@x/server"] } } });
  });

  it("picks models", () => {
    expect(recommendedModel([{ id: "a", label: "A" }, { id: "b", label: "B", recommended: true }])).toBe("b");
    expect(recommendedModel([])).toBe("");
    expect(modelOptions([], "m1")).toEqual([{ id: "m1", label: "m1" }]);
    expect(modelOptions(undefined, "")).toEqual([]);
    expect(showsBaseUrl(undefined, "custom")).toBe(true);
    expect(showsBaseUrl({ id: "x", label: "x", base_url: null, models: [] }, "x")).toBe(false);
    expect(isLargeDownload("2.2GB")).toBe(true);
    expect(isLargeDownload("90MB")).toBe(false);
  });

  it("flattens Neo4j stats", () => {
    expect(neo4jSummary({ enabled: true, reachable: true, stats: { compound: 3, formulation: 2, contains_rels: 4 } }))
      .toEqual({ enabled: true, reachable: true, compounds: 3, formulations: 2, nodes: 5, edges: 4 });
    expect(neo4jSummary({ reachable: false }).nodes).toBeUndefined();
  });
});
