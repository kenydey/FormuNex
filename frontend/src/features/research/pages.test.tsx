import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const call = vi.fn();
vi.mock("@/api/client", () => ({
  call: (...a: unknown[]) => call(...a),
  download: vi.fn(),
  ApiError: class ApiError extends Error { constructor(public status: number, public detail: unknown) { super(String(detail)); } },
}));
vi.mock("@/api/auth", () => ({ authFetch: vi.fn() }));
vi.mock("@/api/stream", () => ({ awaitTask: vi.fn(), cancelTask: vi.fn(), postSse: vi.fn(), thinkingSteps: () => [] }));
vi.mock("@/app/session", () => ({ useSession: () => ({ me: { id: "u", username: "u", display_name: "U" }, can: () => true }) }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null }) => unknown) => sel({ projectId: "p1" }) }));
vi.mock("@/shared/Markdown", () => ({ Markdown: ({ content }: { content: string }) => <div>{content}</div> }));

import "@/app/i18n";
import ResearchPage from "./ResearchPage";
import ArtifactsPage from "./ArtifactsPage";
import { useResearch } from "./store";

function wrap(ui: ReactNode, path: string, route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path={route} element={ui} /></Routes>
        </MemoryRouter>
      </App>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  call.mockReset();
  useResearch.setState({ sources: [], selected: [], turns: [], report: null });
});

describe("ResearchPage", () => {
  it("renders connectors with their availability and the gathered sources", async () => {
    call.mockImplementation((_m: string, path: string) => {
      if (path === "/api/search/status") return Promise.resolve({ patents: { available: true }, literature: { available: false, reason: "no key" } });
      if (path.startsWith("/api/projects/")) return Promise.resolve({ id: "p1", domain: "degreaser", workspace: { requirement: null } });
      return Promise.resolve({});
    });
    useResearch.getState().addSources([{ source: "patents", identifier: "CN123", title: "防锈专利", snippet: "s", relevance: 0.9 }]);
    wrap(<ResearchPage />, "/knowledge/research", "/knowledge/research");
    expect(await screen.findByText("防锈专利")).toBeInTheDocument();
    expect(screen.getByText("专利")).toBeInTheDocument();
    expect(screen.getByText("外部检索（1）")).toBeInTheDocument();
    await waitFor(() => expect(call).toHaveBeenCalledWith("GET", "/api/search/status"));
  });

  it("shows an empty grounded Q&A when nothing is selected", async () => {
    call.mockResolvedValue({});
    wrap(<ResearchPage />, "/knowledge/research?tab=chat", "/knowledge/research");
    expect((await screen.findAllByText(/先在「外部检索」页检索或上传资料/)).length).toBeGreaterThan(0);
  });
});

describe("ArtifactsPage", () => {
  it("loads a lineage's versions from the route", async () => {
    call.mockImplementation((_m: string, path: string) => {
      if (path === "/api/artifacts/lineages/lin1/versions") return Promise.resolve({
        lineage: { lineage_id: "lin1", project_id: "p1", name: "深度研究 · 测试", kind: "report", created_at: 0, version_ids: ["v1aaaaaaaa"] },
        versions: [{ version_id: "v1aaaaaaaa", lineage_id: "lin1", based_on_version_id: null, status: "pending", sha256: "x", content_bytes: 10, created_at: 1 }],
        graph: [],
      });
      return Promise.resolve({});
    });
    wrap(<ArtifactsPage />, "/knowledge/research/artifacts/lin1", "/knowledge/research/artifacts/:lineageId");
    expect(await screen.findByText("深度研究 · 测试")).toBeInTheDocument();
    expect(screen.getByText("待审")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /定\s?版/ })).toBeInTheDocument();
    await waitFor(() => expect(call).toHaveBeenCalledWith("GET", "/api/artifacts/lineages?project_id=p1"));
  });

  it("offers the project's lineages in a picker", async () => {
    call.mockImplementation((_m: string, path: string) => {
      if (path.startsWith("/api/artifacts/lineages?")) return Promise.resolve([{ lineage_id: "lin2abcdef", project_id: "p1", kind: "report",
        title: "配方报告", version_count: 1, latest_version: 1, latest_version_id: "v", status: "staging", created_at: 1, updated_at: 2 }]);
      return Promise.resolve({});
    });
    wrap(<ArtifactsPage />, "/knowledge/research/artifacts?tab=versions", "/knowledge/research/artifacts");
    const select = await screen.findByTestId("lineage-select");
    fireEvent.mouseDown(select.querySelector(".ant-select-selector")!);
    expect(await screen.findByText("配方报告")).toBeInTheDocument();
  });
});
