import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));

const product = {
  id: "p1", code: "PB-1", name_zh: "探针产品", name_en: "", lifecycle_state: "rd", project_id: null,
  category_identifier: null, legacy_code: null, revisions: [], batches: [],
};

function wrap(path: string, route: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter initialEntries={[path]}>
    <Routes><Route path={route} element={el} /></Routes></MemoryRouter></AntApp></QueryClientProvider>);
}

beforeEach(() => call.mockReset());

function mockCalls(versions: unknown[]) {
  call.mockImplementation(async (m: string, p?: string) => {
    const path = String(p ?? "");
    if (path.startsWith("/api/plm/products/") && m === "GET") return product;
    if (path.startsWith("/api/formulations/versions")) return versions;
    if (path.startsWith("/api/provenance")) return { edges: [] };
    if (m === "POST" && path.endsWith("/revisions")) return { id: "rev1" };
    return {};
  });
}

describe("ProductDetailPage revision modal", () => {
  it("shows empty-state guidance when no formulation version exists", async () => {
    mockCalls([]);
    const { default: Page } = await import("./ProductDetailPage");
    wrap("/plm/products/p1", "/plm/products/:productId", <Page />);
    expect(await screen.findByText(/探针产品/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建修订版" }));
    expect(await screen.findByText(/暂无配方版本/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去配方编辑" })).toHaveAttribute("href", "/lab/formulations");
  });

  it("submits without a formulation version (field is optional)", async () => {
    mockCalls([]);
    const { default: Page } = await import("./ProductDetailPage");
    wrap("/plm/products/p1", "/plm/products/:productId", <Page />);
    expect(await screen.findByText(/探针产品/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建修订版" }));
    await screen.findByText(/暂无配方版本/);
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/plm/products/p1/revisions", expect.anything()));
    const body = call.mock.calls.find((c) => c[0] === "POST" && String(c[1]).endsWith("/revisions"))![2] as Record<string, unknown>;
    expect(body["formulation_version_id"]).toBeUndefined();
  });

  it("lists versions and submits the selected one", async () => {
    mockCalls([{ lineage_id: "L1", versions: [{ id: "v1", version: 1, name: "探针配方" }] }]);
    const { default: Page } = await import("./ProductDetailPage");
    wrap("/plm/products/p1", "/plm/products/:productId", <Page />);
    expect(await screen.findByText(/探针产品/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建修订版" }));
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    fireEvent.click(await screen.findByText("探针配方 v1"));
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/plm/products/p1/revisions", expect.objectContaining({ formulation_version_id: "v1" })));
  });
});
