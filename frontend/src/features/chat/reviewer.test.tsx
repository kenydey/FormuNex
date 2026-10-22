import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const call = vi.fn();
vi.mock("@/api/client", () => ({
  call: (...a: unknown[]) => call(...a),
  ApiError: class ApiError extends Error { constructor(public status: number, public detail: unknown) { super(String(detail)); } },
}));
vi.mock("@/api/auth", () => ({ authFetch: vi.fn() }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null }) => unknown) => sel({ projectId: null }) }));

import "@/app/i18n";
import { ReviewerAuditPanel } from "./Reviewer";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><App><MemoryRouter><Routes><Route path="*" element={ui} /></Routes></MemoryRouter></App></QueryClientProvider>,
  );
}

beforeEach(() => call.mockReset());

describe("ReviewerAuditPanel without a project (P1-3)", () => {
  it("guides to pick a project instead of firing the fail-closed 400 list request", async () => {
    wrap(<ReviewerAuditPanel projectId={null} />);
    // Alert message + Empty description share the same copy.
    expect((await screen.findAllByText("请先选择项目")).length).toBeGreaterThanOrEqual(1);
    expect(call).not.toHaveBeenCalledWith(
      "GET", expect.stringContaining("/api/reviews/runs"),
    );
  });

  it("lists runs for the selected project", async () => {
    call.mockImplementation(() => Promise.resolve({ items: [
      { run_id: "run-1", session_key: "k", project_id: "p1", status: "done", outcome: "pass", warn_count: 0, fail_count: 0, unaddressed_count: 0 },
    ] }));
    wrap(<ReviewerAuditPanel projectId="p1" />);
    await waitFor(() => expect(call).toHaveBeenCalledWith(
      "GET", expect.stringContaining("project_id=p1"),
    ));
    expect(await screen.findByText("run-1")).toBeInTheDocument();
  });
});
