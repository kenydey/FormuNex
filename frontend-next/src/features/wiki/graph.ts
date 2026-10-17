/** Pure helpers for the wiki [[wikilink]] graph and the materials KG canvas (ECharts ``graph`` series). */
import type { WikiPageGraphEdge } from "./types";

export type GraphColorMode = "kind" | "community";

export interface GraphNodeLike { id: string; path?: string; label?: string; kind?: string; degree?: number; community?: number }

export type GraphFilter = { query?: string; hideOrphan?: boolean; kinds?: string[] };

export const WIKI_KINDS = ["material", "chemical", "system", "mechanism", "pitfall", "theme", "report"] as const;

/** Filter nodes by kind / orphan / text and keep only edges between kept nodes. */
export function filterGraph<N extends GraphNodeLike>(nodes: N[], edges: WikiPageGraphEdge[], filter: GraphFilter): { nodes: N[]; edges: WikiPageGraphEdge[] } {
  const q = (filter.query || "").trim().toLowerCase();
  const kindSet = filter.kinds?.length ? new Set(filter.kinds.map((k) => k.toLowerCase())) : null;
  let kept = nodes;
  if (kindSet) kept = kept.filter((n) => kindSet.has((n.kind || "").toLowerCase()));
  if (filter.hideOrphan) kept = kept.filter((n) => (n.degree ?? 0) > 0);
  if (q) kept = kept.filter((n) => `${n.label || ""} ${n.path || ""} ${n.kind || ""}`.toLowerCase().includes(q));
  const ids = new Set(kept.map((n) => n.id || n.path));
  return { nodes: kept, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
}

// Mid-saturation categorical palette: legible on both the light and the dark theme.
const KIND_COLORS: Record<string, string> = {
  material: "#1677ff", chemical: "#722ed1", system: "#13a8a8", mechanism: "#d48806",
  pitfall: "#f5222d", theme: "#eb2f96", report: "#8c8c8c",
};
const PALETTE = ["#1677ff", "#722ed1", "#13a8a8", "#d48806", "#f5222d", "#eb2f96", "#52c41a", "#2f54eb", "#fa8c16", "#c41d7f"];

export function kindColor(kind: string | undefined): string {
  const k = (kind || "").toLowerCase();
  if (KIND_COLORS[k]) return KIND_COLORS[k];
  let h = 0;
  for (const c of k) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return k ? PALETTE[h % PALETTE.length] : "#8c8c8c";
}

export function communityColor(community: number | undefined | null): string {
  const n = Number.isFinite(community) ? Math.abs(Math.trunc(community as number)) : 0;
  return PALETTE[n % PALETTE.length];
}

export function nodeFill(node: GraphNodeLike, mode: GraphColorMode = "kind"): string {
  return mode === "community" ? communityColor(node.community) : kindColor(node.kind);
}

/** Map edge weight (~1–2) to a line width in [1, 4]. */
export function edgeWidth(weight?: number): number {
  const w = typeof weight === "number" && Number.isFinite(weight) ? weight : 1;
  return Math.max(1, Math.min(4, 0.8 + w * 1.2));
}

export const nodeSize = (degree?: number) => 12 + Math.min(20, (degree ?? 0) * 3);

export interface GraphSeriesData {
  categories: { name: string; itemStyle: { color: string } }[];
  data: { id: string; name: string; value: number; category: number; symbolSize: number; path?: string; kind?: string; community?: number }[];
  links: { source: string; target: string; value: number; relation?: string; lineStyle: { width: number } }[];
}

/** Nodes + edges → ECharts graph series data, one category (legend entry) per kind or community. */
export function toGraphSeries(nodes: GraphNodeLike[], edges: WikiPageGraphEdge[], mode: GraphColorMode = "kind"): GraphSeriesData {
  const catName = (n: GraphNodeLike) => (mode === "community" ? `#${n.community ?? 0}` : n.kind || "?");
  const names: string[] = [];
  for (const n of nodes) if (!names.includes(catName(n))) names.push(catName(n));
  const categories = names.map((name) => {
    const sample = nodes.find((n) => catName(n) === name)!;
    return { name, itemStyle: { color: nodeFill(sample, mode) } };
  });
  const ids = new Set<string>();
  const data = nodes.filter((n) => {
    const id = n.id || n.path || "";
    if (!id || ids.has(id)) return false;
    ids.add(id);
    return true;
  }).map((n) => ({
    id: n.id || n.path || "", name: n.label || n.path || n.id, value: n.degree ?? 0, category: names.indexOf(catName(n)),
    symbolSize: nodeSize(n.degree), path: n.path, kind: n.kind, community: n.community,
  }));
  const links = edges.filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ source: e.source, target: e.target, value: e.weight ?? 1, relation: e.relation_type, lineStyle: { width: edgeWidth(e.weight) } }));
  return { categories, data, links };
}
