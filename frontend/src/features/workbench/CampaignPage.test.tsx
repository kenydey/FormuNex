import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import CampaignPage from "./CampaignPage";

vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));
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
    if (path.endsWith("/attachment-counts")) return { campaign_id: 7, counts: { "1": 3, "2": 0 } };
    if (path.endsWith("/versions")) return { refcode: null, source: "native", versions: [] };
    if (path.endsWith("/attachments")) return [];
    if (path.endsWith("/quality")) return { stale_count: 1, stale_refs: [], errors_count: 0, dropped_total: 0 };
    if (path.includes("bias-trend")) return { campaign_id: 7, trend: [], alerts: [], threshold_rmse: 50 };
    if (path === "/api/projects/p1") return { id: "p1", title: "P", headline: "", domain: "degreaser", created_at: "", updated_at: "", workspace: { requirement: null } };
    if (path === "/api/training-status") return { total_records: 10, min_samples: 4, sufficient: true, models_trained: 1 };
    if (path === "/health") return { datalab: { ledger_mode: "local" } };
    return null;
  }),
  download: vi.fn(),
  ApiError: class ApiError extends Error { status = 0 },
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><App><MemoryRouter initialEntries={["/cockpit/workbench/7"]}>
    <Routes><Route path="/cockpit/workbench/:campaignId" element={<CampaignPage />} /></Routes>
  </MemoryRouter></App></QueryClientProvider>);
}

describe("CampaignPage", () => {
  it("renders the ledger rows, loop status and data-quality banner", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><App><MemoryRouter initialEntries={["/cockpit/workbench/7"]}>
      <Routes><Route path="/cockpit/workbench/:campaignId" element={<CampaignPage />} /></Routes>
    </MemoryRouter></App></QueryClientProvider>);
    expect(await screen.findByText("#7 · Round A")).toBeInTheDocument();
    expect(await screen.findByText("已入训练")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1.1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("600")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-loop-status")).toHaveTextContent("闭环");
    expect(await screen.findByText("清理失效引用")).toBeInTheDocument();
  });

  it("shows batch attachment counts, undoes / redoes unsaved edits and opens the row menu on right-click", async () => {
    renderPage();
    expect(await screen.findByTestId("att-count-1", {}, { timeout: 15_000 })).toHaveTextContent("3");
    expect(screen.getByTestId("att-count-2")).toHaveTextContent("0");

    const undoBtn = screen.getByRole("button", { name: "撤销" });
    const redoBtn = screen.getByRole("button", { name: "重做" });
    expect(undoBtn).toBeDisabled();
    const salt = screen.getByDisplayValue("600");
    fireEvent.change(salt, { target: { value: "650" } });
    await waitFor(() => expect(screen.getByDisplayValue("650")).toBeInTheDocument());
    expect(screen.getByText(/行未保存$/)).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await waitFor(() => expect(screen.getByDisplayValue("600")).toBeInTheDocument());
    expect(screen.queryByText(/行未保存$/)).toBeNull();
    expect(redoBtn).not.toBeDisabled();
    fireEvent.click(redoBtn);
    await waitFor(() => expect(screen.getByDisplayValue("650")).toBeInTheDocument());
    fireEvent.click(undoBtn);
    await waitFor(() => expect(screen.getByDisplayValue("600")).toBeInTheDocument());

    const row = document.querySelector('tr[data-row-key="2"]')!;
    fireEvent.contextMenu(row);
    fireEvent.click(await screen.findByText("版本历史", { selector: ".ant-dropdown-menu-title-content" }));
    expect(await screen.findByText(/^实验行/)).toBeInTheDocument();
    // native rows (no refcode) now load their audit-log versions instead of a blank "no ELN" placeholder
    expect(await screen.findByText(/本地台账：每次保存台账时/)).toBeInTheDocument();
  }, 60_000);
});
