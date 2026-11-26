import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
let perms: string[] = [];
vi.mock("@/app/session", () => ({ useSession: () => ({ can: (p: string) => perms.includes(p) }) }));

import ExcelImportModal from "./ExcelImportModal";

function renderModal(kind: "formulas" | "companies" = "formulas") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AntApp>
        <ExcelImportModal open onClose={() => undefined} kind={kind} />
      </AntApp>
    </QueryClientProvider>,
  );
}

function pickFile() {
  // The Modal renders in a portal on document.body, not inside the render container.
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "import.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" })] } });
}

beforeEach(() => {
  perms = [];
  call.mockReset();
  call.mockResolvedValue({ dry_run: true, total: 0, created: [], skipped: [], errors: [], warnings: [] });
});

describe("ExcelImportModal (X-P3-2)", () => {
  it("disables the submit button without plm.document.write, even with a file picked", async () => {
    renderModal();
    expect(await screen.findByText("Excel 导入配方")).toBeInTheDocument();
    pickFile();
    await waitFor(() => expect(screen.getByText("import.xlsx")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /预\s?览/ })).toBeDisabled();
    expect(call).not.toHaveBeenCalled();
  });

  it("submits the dry-run preview with plm.document.write", async () => {
    perms = ["plm.document.write"];
    renderModal();
    expect(await screen.findByText("Excel 导入配方")).toBeInTheDocument();
    pickFile();
    await waitFor(() => expect(screen.getByText("import.xlsx")).toBeInTheDocument());
    const submit = screen.getByRole("button", { name: /预\s?览/ });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(call).toHaveBeenCalledWith(
      "POST", "/api/plm/import/formulas?dry_run=true", expect.any(FormData),
    ));
  });

  it("gates the companies import the same way", async () => {
    perms = ["admin.companies"];
    renderModal("companies");
    expect(await screen.findByText("Excel 导入公司")).toBeInTheDocument();
    pickFile();
    await waitFor(() => expect(screen.getByText("import.xlsx")).toBeInTheDocument());
    // admin.companies alone does not imply plm.document.write (sys_admin):
    // the backend import endpoint requires plm.document.write.
    expect(screen.getByRole("button", { name: /预\s?览/ })).toBeDisabled();
    expect(call).not.toHaveBeenCalled();
  });
});
