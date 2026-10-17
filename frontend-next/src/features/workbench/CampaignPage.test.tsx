import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import CampaignPage from "./CampaignPage";

vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("echarts-for-react", () => ({ default: () => null }));
vi.mock("@/api/client", () => ({
  call: vi.fn(async (_m: string, path: string) => {
    if (path === "/api/experiments/workbench/7") return {
      campaign_id: 7, name: "Round A", strategy: "DOE-ccd", status: "active", project_id: "p1",
      objectives_snapshot: [{ metric: "salt", direction: "maximize", ref_min: 500 }], loop_history: [], loop_status: { status: "idle", rounds: 2 },
      rows: [
        { id: 1, campaign_id: 7, status: "Completed", planned_params: { A: 1, B: 2 }, actual_params: { A: 1.1 }, measurements: { salt: 600 }, ingested: true },
        { id: 2, campaign_id: 7, status: "Pending", planned_params: { A: 3, B: 4 }, actual_params: {}, measurements: {} },
      ],
    };
    if (path.endsWith("/quality")) return { stale_count: 1, stale_refs: [], errors_count: 0, dropped_total: 0 };
    if (path.includes("bias-trend")) return { campaign_id: 7, trend: [], alerts: [], threshold_rmse: 50 };
    if (path === "/api/projects/p1") return { id: "p1", title: "P", headline: "", domain: "degreaser", created_at: "", updated_at: "", workspace: { requirement: null } };
    if (path === "/api/training-status") return { total_records: 10, min_samples: 4, sufficient: true, models_trained: 1 };
    if (path === "/health") return { datalab: { ledger_mode: "local" } };
    return null;
  }),
  download: vi.fn(),
}));

describe("CampaignPage", () => {
  it("renders the ledger rows, loop status and data-quality banner", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><App><MemoryRouter initialEntries={["/cockpit/workbench/7"]}>
      <Routes><Route path="/cockpit/workbench/:campaignId" element={<CampaignPage />} /></Routes>
    </MemoryRouter></App></QueryClientProvider>);
    expect(await screen.findByText("#7 · Round A")).toBeInTheDocument();
    expect(await screen.findByText(/1\/2 已完成/)).toBeInTheDocument();
    expect(screen.getByTestId("workbench-loop-status")).toHaveTextContent("闭环 2 轮");
    expect(await screen.findByText("失效引用 1")).toBeInTheDocument();
  });
});
