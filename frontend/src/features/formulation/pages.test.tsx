import { render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/shared/Chart", () => ({ Chart: () => <div data-testid="chart" /> }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));

function wrap(path: string, route: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter initialEntries={[path]}>
    <Routes><Route path={route} element={el} /></Routes></MemoryRouter></AntApp></QueryClientProvider>);
}

beforeEach(() => call.mockReset());

describe("ExperimentsPage", () => {
  it("lists training experiments of the project", async () => {
    call.mockResolvedValue([{ id: 7, domain: "degreaser", label: "batch-7", source: "eln", project_id: "p1", measured: { ph: 9 }, measurement_count: 3, created_at: "2026-01-02T03:04:00" }]);
    const { default: Page } = await import("./ExperimentsPage");
    wrap("/lab/experiments", "/lab/experiments", <Page />);
    expect(await screen.findByText("batch-7")).toBeInTheDocument();
    expect(screen.getByText("#7")).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("GET", "/api/experiments?project_id=p1&limit=100");
  });
});

describe("LineagePage", () => {
  it("shows the revisions and diffs a version with its parent", async () => {
    const versions = [
      { id: "a", lineage_id: "L1", version: 1, parent_version_id: null, name: "Epoxy", domain: "d", change_summary: "first", created_by: "", created_at: null },
      { id: "b", lineage_id: "L1", version: 2, parent_version_id: "a", name: "Epoxy", domain: "d", change_summary: "more zinc", created_by: "u", created_at: null },
    ];
    call.mockImplementation((_m: string, p?: string) => {
      const path = String(p ?? "");
      if (path.includes("/diff/")) return Promise.resolve({ from_version: 1, to_version: 2, change_summary: "", topology_changed: true, renamed: null,
        ingredient_changes: [{ name: "Zinc", change: "added", before_pct: null, after_pct: 5, delta_pct: 5 }], metric_deltas: {} });
      if (path.startsWith("/api/provenance")) return Promise.resolve({ edges: [] });
      return Promise.resolve({ lineage_id: "L1", versions });
    });
    const { default: Page } = await import("./LineagePage");
    wrap("/lab/lineage/L1", "/lab/lineage/:lineageId", <Page />);
    expect(await screen.findByText("more zinc")).toBeInTheDocument();
    screen.getByRole("button", { name: "与上一版对比" }).click();
    expect(await screen.findByText("Zinc")).toBeInTheDocument();
    expect(screen.getByText("成分组成变化")).toBeInTheDocument();
    await waitFor(() => expect(call).toHaveBeenCalledWith("GET", "/api/formulations/versions/a/diff/b"));
  });
});

describe("ExperimentDetailPage", () => {
  it("loads the experiment by id instead of scanning the list", async () => {
    call.mockImplementation(async (_m: string, p?: string) => {
      const path = String(p ?? "");
      if (path === "/api/experiments/42") return { id: 42, domain: "degreaser", label: "Batch 42", source: "lab", project_id: "p1",
        measured: { ph: 9.1 }, measurement_count: 0, created_at: "2026-10-01T08:00:00" };
      if (path.includes("/measurements")) return { experiment_id: 42, measurements: [] };
      if (path.endsWith("/attachments")) return [];
      return null;
    });
    const { default: Detail } = await import("./ExperimentDetailPage");
    wrap("/lab/experiments/42", "/lab/experiments/:id", <Detail />);
    expect(await screen.findByText("Batch 42")).toBeInTheDocument();
    expect(await screen.findByText("9.1")).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("GET", "/api/experiments/42");
    expect(call).not.toHaveBeenCalledWith("GET", expect.stringContaining("limit=1000"));
  });
});
