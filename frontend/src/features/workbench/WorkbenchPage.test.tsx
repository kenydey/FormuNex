import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import WorkbenchPage from "./WorkbenchPage";

vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string }) => unknown) => sel({ projectId: "p1" }) }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/api/client", () => ({
  call: vi.fn(async (_m: string, path: string) => {
    if (path === "/api/experiments/workbench/campaigns") {
      return [
        { id: 7, name: "Round A", status: "active", strategy: "DOE-ccd", row_count: 9, project_id: "p1" },
        { id: 8, name: "Other project", status: "active", strategy: "", row_count: 2, project_id: "p2" },
      ];
    }
    if (path === "/api/projects/p1") return { id: "p1", title: "P", headline: "", domain: "degreaser", created_at: "", updated_at: "",
      workspace: { requirement: { domain: "degreaser", objectives: [] }, workbench_campaign_id: 7 } };
    if (path === "/api/training-status") return { total_records: 1, min_samples: 4, sufficient: false, models_trained: 0 };
    if (path === "/api/meta") return { designs: ["ccd"], engines: {} };
    return null;
  }),
  download: vi.fn(),
}));

describe("WorkbenchPage", () => {
  it("lists the project's campaigns and the training-data warning", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><App><MemoryRouter><WorkbenchPage /></MemoryRouter></App></QueryClientProvider>);
    expect(await screen.findByText("Round A")).toBeInTheDocument();
    expect(screen.queryByText("Other project")).toBeNull();
    expect(await screen.findByText(/训练数据不足/)).toBeInTheDocument();
    expect(screen.getByText("项目活动台账")).toBeInTheDocument();
  });
});
