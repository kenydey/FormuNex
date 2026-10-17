import { describe, expect, it } from "vitest";
import { communityColor, edgeWidth, filterGraph, kindColor, nodeFill, toGraphSeries } from "./graph";
import type { WikiPageGraphEdge, WikiPageGraphNode } from "./types";

const nodes: WikiPageGraphNode[] = [
  { id: "a", path: "materials/a.md", label: "Alpha", kind: "material", degree: 2, community: 0 },
  { id: "b", path: "materials/b.md", label: "Beta", kind: "material", degree: 1, community: 0 },
  { id: "c", path: "themes/c.md", label: "Theme C", kind: "theme", degree: 0, community: 1 },
];
const edges: WikiPageGraphEdge[] = [{ source: "a", target: "b", weight: 1.5 }, { source: "a", target: "zz" }];

describe("filterGraph", () => {
  it("hides orphans", () => {
    const out = filterGraph(nodes, edges, { hideOrphan: true });
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    expect(out.edges).toHaveLength(1);
  });
  it("filters by query and kind", () => {
    expect(filterGraph(nodes, edges, { query: "theme" }).nodes.map((n) => n.path)).toEqual(["themes/c.md"]);
    expect(filterGraph(nodes, edges, { kinds: ["theme"] }).edges).toHaveLength(0);
  });
});

describe("colours", () => {
  it("are stable hex colours", () => {
    expect(kindColor("material")).toMatch(/^#/);
    expect(kindColor("unknown-xyz")).toBe(kindColor("unknown-xyz"));
    expect(communityColor(99)).toMatch(/^#/);
    expect(nodeFill(nodes[0], "kind")).toBe(kindColor("material"));
    expect(nodeFill(nodes[2], "community")).toBe(communityColor(1));
  });
  it("maps edge weight into [1, 4]", () => {
    expect(edgeWidth(1)).toBeGreaterThanOrEqual(1);
    expect(edgeWidth(2)).toBeGreaterThan(edgeWidth(1));
    expect(edgeWidth(99)).toBe(4);
  });
});

describe("toGraphSeries", () => {
  it("builds categories per kind / community and drops dangling links", () => {
    const s = toGraphSeries(nodes, edges, "kind");
    expect(s.categories.map((c) => c.name)).toEqual(["material", "theme"]);
    expect(s.data.map((d) => d.category)).toEqual([0, 0, 1]);
    expect(s.links).toHaveLength(1);
    expect(s.data[0].symbolSize).toBeGreaterThan(s.data[2].symbolSize);
    expect(toGraphSeries(nodes, edges, "community").categories.map((c) => c.name)).toEqual(["#0", "#1"]);
  });
});
