import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
vi.mock("@/api/stream", () => ({ awaitTask: vi.fn(), cancelTask: vi.fn() }));
vi.mock("@/app/session", () => ({ useSession: () => ({ me: { id: "u" }, can: () => true }) }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("@/shared/Markdown", () => ({ Markdown: ({ content }: { content: string }) => <pre>{content}</pre>, SmilesDrawing: () => null }));
vi.mock("@/shared/Chart", () => ({ Chart: () => null }));

import "@/app/i18n";
import SourcesPage from "@/features/knowledge/SourcesPage";
import ManifestPage from "./ManifestPage";
import MemoryPage from "./MemoryPage";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><App><MemoryRouter><Routes><Route path="*" element={ui} /></Routes></MemoryRouter></App></QueryClientProvider>,
  );
}

beforeEach(() => call.mockReset());

describe("SourcesPage", () => {
  it("lists the project's documents and archives one", async () => {
    call.mockImplementation((m: string, path = "") => { if (!path) console.log("CALLARGS", m);
      if (path.startsWith("/api/kb/sources?")) return Promise.resolve({ total: 1, sources: [
        { id: "s1", title: "水性环氧专利", filename: "a.pdf", source_kind: "patent", project_id: "p1", raw_text_chars: 1200, extraction_status: "ok", archived: false }] });
      if (path === "/api/formulations/embodiment-eligibility") return Promise.resolve({ items: [{ source_id: "s1", eligible: true }] });
      if (m === "POST" && path.endsWith("/archive")) return Promise.resolve({ ok: true });
      return Promise.resolve({});
    });
    wrap(<SourcesPage />);
    expect(await screen.findByText("水性环氧专利")).toBeInTheDocument();
    expect(call.mock.calls[0][1]).toContain("project_id=p1");
    expect(await screen.findByText("提取实施例草稿")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /归\s?档/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/kb/sources/s1/archive", { archived: true }));
  });
});

describe("ManifestPage", () => {
  it("shows coverage of the frozen subset", async () => {
    call.mockImplementation((_m: string, path = "") => { if (!path) console.log("CALLARGS", _m);
      if (path === "/api/wiki/literature/p1") return Promise.resolve({
        project_id: "p1", coverage: { candidate_count: 2, frozen_count: 1 },
        frozen: { at: 1700000000, actor: "hub", item_ids: ["a"], digest: "abcdef123456" },
        items: [{ id: "a", title: "冻结论文", doi: "10.1/x", screening: "match" }, { id: "b", title: "候选论文" }],
      });
      if (path.includes("presets")) return Promise.resolve({ presets: [] });
      if (path.includes("rule-versions")) return Promise.resolve({ versions: [], history: [] });
      return Promise.resolve({});
    });
    wrap(<ManifestPage />);
    expect((await screen.findAllByText("冻结论文")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "候选论文" })).toBeNull();
    expect(screen.getByText("冻结条目")).toBeInTheDocument();
  });
});

describe("MemoryPage", () => {
  it("renders memories with their scope", async () => {
    call.mockResolvedValue({ total: 1, items: [{ id: 1, scope: "project", scope_id: "p1", key: "偏好", value: "低VOC", created_at: "", updated_at: "2026-01-01T00:00:00Z" }] });
    wrap(<MemoryPage />);
    expect(await screen.findByText("偏好")).toBeInTheDocument();
    expect(screen.getByText("项目 · p1")).toBeInTheDocument();
  });
});
