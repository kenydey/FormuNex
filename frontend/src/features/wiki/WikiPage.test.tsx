import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const pages = [
  { id: "1", path: "materials/e51.md", kind: "material", title: "E-51 环氧树脂", norm_key: "e51", flags: ["stale"] },
  { id: "2", path: "systems/epoxy.md", kind: "system", title: "环氧体系", norm_key: "epoxy" },
];

vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("@/shared/Markdown", () => ({ Markdown: ({ content }: { content: string }) => <pre data-testid="md">{content}</pre> }));
vi.mock("@/api/client", () => ({
  ApiError: class extends Error {},
  download: vi.fn(),
  call: vi.fn(async (_m: string, path: string) => {
    if (path.startsWith("/api/wiki/pages?")) return { pages, total: 2 };
    if (path.startsWith("/api/wiki/by-path")) return { ...pages[0], markdown: "---\nsource_ids: [src-1]\n---\n# E-51\n\n用于 [[system:epoxy|环氧体系]] 与 [[Missing]]。", source_ids: [] };
    if (path.startsWith("/api/settings/env-flags")) return { flags: [{ attr: "wiki_embed_enabled", value: true }] };
    throw new Error(`unexpected ${path}`);
  }),
}));

import WikiPage from "./WikiPage";

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App>
        <MemoryRouter initialEntries={[url]}>
          <Routes><Route path="/knowledge/wiki/*" element={<WikiPage />} /></Routes>
        </MemoryRouter>
      </App>
    </QueryClientProvider>,
  );
}

describe("WikiPage", () => {
  it("lists project pages and reads the selected page with wikilinks rewritten", async () => {
    renderAt("/knowledge/wiki/materials/e51.md");
    expect(await screen.findByRole("link", { name: "环氧体系" }, { timeout: 8000 })).toHaveAttribute("href", "/knowledge/wiki/systems/epoxy.md");
    const md = await screen.findByTestId("md");
    expect(md.textContent).toContain("[环氧体系](/knowledge/wiki/systems/epoxy.md)");
    expect(md.textContent).toContain("(#wiki-dead:Missing)");
    expect(screen.getAllByText("E-51 环氧树脂").length).toBeGreaterThan(0);
    expect(screen.getByText("src-1")).toBeInTheDocument();
    expect(screen.getByText("Embed 已开启")).toBeInTheDocument();
  });

  it("asks to pick a page when none is selected", async () => {
    renderAt("/knowledge/wiki");
    expect(await screen.findByText(/选择左侧页面查看编译记忆/)).toBeInTheDocument();
  });
});
