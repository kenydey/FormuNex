import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("@/shared/Chart", () => ({
  Chart: ({ option, onEvents }: { option: { series: { data: { id: string; name: string }[] }[] }; onEvents?: Record<string, (p: unknown) => void> }) => (
    <div>{option.series[0].data.map((d) => (
      <button key={d.id} onClick={() => onEvents?.click({ dataType: "node", data: d })}>node:{d.name}</button>
    ))}</div>
  ),
}));
vi.mock("@/api/client", () => ({
  ApiError: class extends Error {},
  call: vi.fn(async (_m: string, path: string) => {
    if (path.startsWith("/api/settings/env-flags")) return { flags: [{ attr: "wiki_page_graph_enabled", value: true }] };
    if (path.startsWith("/api/wiki/graph")) return {
      ok: true, meta: { node_count: 2, edge_count: 1, orphan_count: 0 },
      nodes: [{ id: "a", path: "materials/a.md", label: "Alpha", kind: "material", degree: 1 }, { id: "b", path: "systems/b.md", label: "Beta", kind: "system", degree: 1 }],
      edges: [{ source: "a", target: "b" }],
      insights: { orphans: [], isolates: [], broken: [{ source: "materials/a.md", source_label: "Alpha", target: "Gone" }] },
    };
    if (path.startsWith("/api/wiki/by-path")) return { id: "a", path: "materials/a.md", kind: "material", title: "Alpha", markdown: "# Alpha body" };
    if (path.startsWith("/api/kg/graph")) return {
      ok: true, meta: { node_count: 1, edge_count: 0 }, nodes: [{ id: "chem:1", label: "Bisphenol A", kind: "chemical", degree: 0 }], edges: [],
    };
    if (path.startsWith("/api/kg/feedback/stats")) return { measured_total: 3, measured_performance: 2 };
    if (path.startsWith("/api/kg/feedback/report")) return { alert: null };
    if (path.startsWith("/api/kg/resolve")) return { query: "x", chemicals: [], trade_products: [], top_relations: [] };
    throw new Error(`unexpected ${path}`);
  }),
}));

import KgGraphPage from "./KgGraphPage";
import WikiGraphPage from "./WikiGraphPage";

const wrap = (el: JSX.Element) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <App><MemoryRouter>{el}</MemoryRouter></App>
  </QueryClientProvider>,
);

describe("graph pages", () => {
  it("wiki link graph: nodes, insights, click opens the reader drawer", async () => {
    wrap(<WikiGraphPage />);
    fireEvent.click(await screen.findByText("node:Alpha"));
    expect(await screen.findByText("Wiki 阅读")).toBeInTheDocument();
    expect(screen.getByText("[[Gone]]")).toBeInTheDocument();
    expect(screen.getByTestId("wiki-graph-meta").textContent).toContain("孤立");
  });

  it("materials KG: selecting a node shows the entity panel", async () => {
    wrap(<KgGraphPage />);
    fireEvent.click(await screen.findByText("node:Bisphenol A"));
    expect(await screen.findByText("chem:1")).toBeInTheDocument();
    expect((await screen.findAllByTestId("kg-feedback-stats")).length).toBeGreaterThan(0);
  });
});
