import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));

function wrap(el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter>{el}</MemoryRouter></AntApp></QueryClientProvider>);
}

const points = {
  plan_id: "plan1abc", status: "pending",
  runs: [{ run_id: 1, natural: { x: 1 } }, { run_id: 2, natural: { x: 2 } }],
  summary: { counts: { pending: 2 }, total: 2, effective_runs: [] },
};

vi.setConfig({ testTimeout: 60000 });

beforeEach(() => {
  call.mockReset();
  call.mockImplementation(async (method: string, path: string) => {
    if (path.startsWith("/api/doe/history")) return { items: [{ plan_id: "plan1abc", design: "factorial", created_at: "2026-01-02T03:04:00" }] };
    if (path === "/api/cockpit/doe/plan1abc/points") return points;
    if (path === "/api/cockpit/optimizer-rules") return { ucb_kappa: 1.5, early_stop_patience: 5, early_stop_min_delta: 1e-4 };
    if (method === "POST" && path === "/api/cockpit/doe/plan1abc/points/review") return { ok: true };
    throw new Error(`unexpected ${method} ${path}`);
  });
});

async function openPlan() {
  const { default: Page } = await import("./PointReviewPage");
  const utils = wrap(<Page />);
  await waitFor(() => expect(call).toHaveBeenCalledWith("GET", expect.stringContaining("/api/doe/history")));
  fireEvent.mouseDown(utils.container.querySelector(".ant-select-selector")!);
  fireEvent.click(await screen.findByText("factorial · 2026-01-02 · plan1abc", { selector: ".ant-select-item-option-content" }, { timeout: 10000 }));
  // 等备注输入渲染出来（而非仅 points 请求发出），否则后续 getAllByRole 会在渲染前执行。
  await screen.findAllByRole("textbox", {}, { timeout: 10000 });
  return utils;
}

async function submittedReviews() {
  fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
  await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/cockpit/doe/plan1abc/points/review", expect.anything()), { timeout: 10000 });
  const posts = call.mock.calls.filter((c) => c[0] === "POST" && c[1] === "/api/cockpit/doe/plan1abc/points/review");
  return (posts[posts.length - 1][2] as { reviews: unknown[] }).reviews;
}

describe("PointReviewPage P1-18", () => {
  it("只填备注不产生 accept 编辑，提交时该行不发送", async () => {
    await openPlan();
    const notes = screen.getAllByRole("textbox");
    fireEvent.change(notes[0], { target: { value: "仅备注" } });
    expect((notes[0] as HTMLInputElement).value).toBe("仅备注");
    expect(await submittedReviews()).toEqual([]);
  });

  it("先备注后显式选采纳，备注随决策一起提交", async () => {
    const utils = await openPlan();
    const notes = screen.getAllByRole("textbox");
    fireEvent.change(notes[0], { target: { value: "随决策的备注" } });
    fireEvent.mouseDown(utils.container.querySelectorAll(".ant-select-selector")[1]);
    fireEvent.click(await screen.findByText("采纳", { selector: ".ant-select-item-option-content" }, { timeout: 10000 }));
    expect(await submittedReviews()).toEqual([{ run_id: 1, decision: "accept", note: "随决策的备注", factors: undefined }]);
  });
});
