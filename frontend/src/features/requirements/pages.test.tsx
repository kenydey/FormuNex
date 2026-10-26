import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(() => Promise.resolve()) }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));

import MaterialsPage from "@/features/materials/MaterialsPage";
import ProjectSourcesPage from "./ProjectSourcesPage";
import { ConstraintsEditor } from "./ConstraintsEditor";

function wrap(ui: ReactNode, path = "/", route = "*") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AntApp>
        <MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

beforeEach(() => { call.mockReset(); });

describe("MaterialsPage", () => {
  it("lists the catalogue and cycles availability", async () => {
    call.mockImplementation((method: string, path: string) => {
      if (path.startsWith("/api/materials/candidates")) return Promise.resolve({ total: 2, candidates: [] });
      if (path.startsWith("/api/materials?")) {
        return Promise.resolve({ total: 1, store_enabled: true, materials: [{ name: "E-51", role: "resin", origin: "user", availability: "in_stock", spec: { cas_no: "25068-38-6", smiles: "C" } }] });
      }
      if (method === "POST" && path === "/api/materials/availability") return Promise.resolve({});
      return Promise.resolve({});
    });
    wrap(<MaterialsPage />);
    expect(await screen.findByText("E-51")).toBeInTheDocument();
    expect(screen.getByText("25068-38-6")).toBeInTheDocument();
    await userEvent.click(screen.getByText("在库"));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/materials/availability", { name: "E-51", availability: "restricted" }));
  });
});

describe("ProjectSourcesPage", () => {
  it("merges evidence with KB sources of the project", async () => {
    call.mockImplementation((_m: string, path: string) => {
      if (path.startsWith("/api/projects/p1")) {
        return Promise.resolve({ id: "p1", title: "P", workspace: { requirement: null, sources: [{ source: "patent", identifier: "CN112345678A", title: "Zeta patent" }], selected_sources: [] } });
      }
      if (path.startsWith("/api/kb/sources")) {
        return Promise.resolve({ total: 2, sources: [
          { id: "s1", title: "x", filename: "", source_kind: "patent", origin_url: "CN112345678A", raw_text_chars: 1, extraction_status: "indexed" },
          { id: "s2", title: "Upload doc", filename: "u.pdf", source_kind: "upload", raw_text_chars: 1, extraction_status: "indexed" },
        ] });
      }
      return Promise.resolve({});
    });
    wrap(<ProjectSourcesPage />, "/spec/projects/p1/sources", "/spec/projects/:projectId/sources");
    expect(await screen.findByText("Zeta patent")).toBeInTheDocument();
    expect(screen.getByText("Upload doc")).toBeInTheDocument();
    expect(call).toHaveBeenCalledWith("GET", "/api/kb/sources?limit=200&project_id=p1");
  });
});

describe("ConstraintsEditor", () => {
  it("shows active catalogue and custom constraints and removes one", async () => {
    const onChange = vi.fn();
    wrap(<ConstraintsEditor requirement={{ domain: "anticorrosion_coating", voc_limit_gpl: 300, constraint_values: { 硬度: 2 } }}
      activeKeys={["voc_limit_gpl"]} onChange={onChange} />);
    expect(screen.getByText("VOC 上限")).toBeInTheDocument();
    expect(screen.getByText("硬度")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /删除|delete/i })[0]);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ voc_limit_gpl: null }), []);
  });
});
