import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const call = vi.fn(async (method: string, path: string, _body?: unknown): Promise<unknown> => {
  if (path.startsWith("/api/experiments/workbench/campaigns") && method === "GET") return [];
  if (path.startsWith("/api/doe/history")) {
    return { total: 2, page: 1, page_size: 20, items: [
      { plan_id: "p-draft", design: "ccd", status: "draft", runs: [{ run_id: 1, coded: {}, natural: {} }], created_at: "2026-10-01T10:00:00" },
      { plan_id: "p-done", design: "lhs", status: "completed", runs: [], created_at: "2026-09-01T10:00:00" },
    ] };
  }
  if (path.startsWith("/api/doe/plans/p-draft")) {
    return { plan_id: "p-draft", design: "ccd", status: "draft", domain: "degreaser", notes: "full plan", project_id: "proj1",
      factors: [{ name: "A", low: 0, high: 1 }], runs: [{ run_id: 1, coded: {}, natural: { A: 0.5 } }], created_at: "2026-10-01T10:00:00" };
  }
  if (path === "/api/projects/proj1") return { id: "proj1", domain: "degreaser", workspace: {} };
  if (path === "/api/experiments/workbench/campaigns" && method === "POST") return { campaign_id: 31, rows: [], objectives_snapshot: [] };
  if (method === "PUT") return { id: "proj1", domain: "degreaser", workspace: {} };
  if (method === "POST") return { plan_id: "p-draft", status: "active" };
  throw new Error(`unexpected ${method} ${path}`);
});
vi.mock("@/api/client", () => ({ call: (m: string, p: string, b?: unknown) => call(m, p, b), download: vi.fn() }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string }) => unknown) => sel({ projectId: "proj1" }) }));
vi.mock("@/features/lab/DispatchButton", () => ({ DispatchButton: () => null }));

import DoeHistoryPage from "./DoeHistoryPage";

describe("DoeHistoryPage", () => {
  it("lists project plans and offers only legal transitions", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><App><DoeHistoryPage /></App></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("ccd")).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("GET", expect.stringContaining("project_id=proj1"), undefined);
    expect(screen.getByText("草稿")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getAllByText("启动执行")).toHaveLength(1);
    expect(screen.getAllByText("终止")).toHaveLength(1); // completed plans have no actions
    fireEvent.click(screen.getByText("启动执行"));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/doe/p-draft/activate", {}));
  });
});

describe("DoeHistoryPage detail drawer", () => {
  it("reads the full plan by id and creates a ledger from it", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/lab/doe/history"]}><App><Routes>
          <Route path="/lab/doe/history" element={<DoeHistoryPage />} />
          <Route path="/cockpit/workbench/:id" element={<div>ledger-opened</div>} />
        </Routes></App></MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText("ccd"));
    expect(await screen.findByText("full plan")).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("GET", "/api/doe/plans/p-draft?project_id=proj1", undefined);
    const adopt = await screen.findByRole("button", { name: "写入实验台账" });
    await waitFor(() => expect(adopt).not.toBeDisabled());
    fireEvent.click(adopt);
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/experiments/workbench/campaigns", expect.objectContaining({
      plan: expect.objectContaining({ plan_id: "p-draft", design: "ccd", runs: [{ run_id: 1, coded: {}, natural: { A: 0.5 } }] }),
      project_id: "proj1" })));
    expect(await screen.findByText("ledger-opened")).toBeInTheDocument();
  });
});
