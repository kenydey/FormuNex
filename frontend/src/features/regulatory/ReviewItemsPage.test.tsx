import { App } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ReviewItemsPage from "./ReviewItemsPage";

vi.mock("@/app/session", () => ({
  useSession: () => ({ can: (p: string) => p === "regulatory.read" || p === "regulatory.review" }),
}));
vi.mock("@/api/client", () => ({
  call: vi.fn(async () => ({
    open_by_kind: { oel_unmatched: 1, seed_drift: 2 },
    items: [
      { id: "1", kind: "oel_unmatched", status: "open", cas_no: "50-00-0", name: "甲醛",
        detail: { source: "GBZ" }, release_id: "r1", resolved_by: null, resolved_at: null },
      { id: "2", kind: "seed_drift", status: "open", cas_no: null, name: "乙醇",
        detail: {}, release_id: "r1", resolved_by: null, resolved_at: null },
    ],
  })),
}));

describe("ReviewItemsPage", () => {
  it("lists queue kinds with counts and resolve actions", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <App><ReviewItemsPage /></App>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("甲醛")).toBeInTheDocument());
    expect(screen.getByText("OEL 未匹配（GBZ）")).toBeInTheDocument();
    expect(screen.getByText("种子漂移")).toBeInTheDocument();
    expect(screen.getAllByText("标记为已处理")).toHaveLength(2);
  });
});
