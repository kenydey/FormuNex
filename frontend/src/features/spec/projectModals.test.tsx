import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null; setProject: (v: string | null) => void }) => unknown) => sel({ projectId: null, setProject: vi.fn() }) }));

function wrap(el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter>{el}</MemoryRouter></AntApp></QueryClientProvider>);
}

beforeEach(() => call.mockReset());

describe("project modals", () => {
  it("creates a project via the shared modal", async () => {
    call.mockResolvedValue({ id: "p9", title: "新项目" });
    const { ProjectCreateModal: Modal } = await import("./ProjectCreateModal");
    wrap(<Modal open onClose={() => {}} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "新项目" } });
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/projects",
      { title: "新项目", requirement: { domain: "anticorrosion_coating", objectives: [] } }));
  });

  it("renames a project with title only", async () => {
    call.mockResolvedValue({});
    const { ProjectRenameModal: Modal } = await import("./ProjectRenameModal");
    wrap(<Modal open onClose={() => {}} project={{ id: "p1", title: "旧名" }} />);
    const input = screen.getByDisplayValue("旧名");
    fireEvent.change(input, { target: { value: "新名" } });
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("PUT", "/api/projects/p1", { title: "新名" }));
  });

  it("deletes with the chosen knowledge handling", async () => {
    call.mockResolvedValue({ ok: true });
    const { ProjectDeleteModal: Modal } = await import("./ProjectDeleteModal");
    const { unmount } = wrap(<Modal open onClose={() => {}} project={{ id: "p1", title: "旧名" }} />);
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("DELETE", "/api/projects/p1?knowledge=delete"));
    unmount();
    call.mockClear();
    wrap(<Modal open onClose={() => {}} project={{ id: "p1", title: "旧名" }} />);
    fireEvent.click(screen.getByText(/保留为全局知识库/));
    fireEvent.click(screen.getByRole("button", { name: /确\s?定|OK/ }));
    await waitFor(() => expect(call).toHaveBeenCalledWith("DELETE", "/api/projects/p1?knowledge=global"));
  });
});
